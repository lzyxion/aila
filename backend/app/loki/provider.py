"""`LokiProvider` — `LogSourceProvider` 의 Loki 구현체.

Loki 고유 지식은 **이 모듈 안에 가둔다.** 밖으로는 `LogRecord` / `FetchResult` /
`CountSeries` 와 표준화된 `FetchWarning` 코드만 올라간다.

여기서 흡수하는 Loki 고유 사정 네 가지:

1. `| json` 파서 스테이지는 파싱 실패 줄에 `__error__` 라벨을 붙여 통과시킨다.
   해당 스트림은 레코드로 올리지 않고 `dropped` 와 `parse_error` 경고로만 알린다.
2. Loki 는 `limit` 을 서버 설정(`max_entries_limit_per_query`, 기본 5,000)으로
   자른다. 요청 limit 을 그 값으로 clamp 하고, 실제 반환량이 유효 limit 에
   닿으면 `truncated=True` + `limit_reached` 경고를 붙인다.
3. 스트림 응답의 타임스탬프는 **나노초 문자열**, matrix 응답은 **초(float)** 다.
4. 건수·추이는 로그 라인을 세지 않고 `count_over_time` metric 쿼리로 구한다.

읽기 전용이다 — 이 모듈은 Loki 에 쓰지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx

from app.providers.logsource import LogSourceError, LogSourceProvider
from app.schemas.logrecord import (
    ConnectionTestResult,
    CountPoint,
    CountSeries,
    FetchResult,
    FetchWarning,
    LogRecord,
    TimeRange,
)

#: Loki 기본 반환 한도(`max_entries_limit_per_query`). 요청 limit 을 여기로 clamp 한다.
LOKI_MAX_ENTRIES: int = 5000

#: 파서 스테이지가 파싱 실패 줄에 붙이는 라벨.
ERROR_LABEL = "__error__"
ERROR_DETAILS_LABEL = "__error_details__"

#: `label_mapping` 이 다루는 표준 필드.
STANDARD_FIELDS: tuple[str, ...] = ("service", "environment", "level")

READY_PATH = "/ready"
LABELS_PATH = "/loki/api/v1/labels"
LABEL_VALUES_PATH = "/loki/api/v1/label/{name}/values"
QUERY_RANGE_PATH = "/loki/api/v1/query_range"

#: 로그 쿼리가 아니라 이미 metric 쿼리로 보이는지 판정하는 패턴.
#: LogQL 로그 쿼리는 항상 스트림 셀렉터 `{...}` 로 시작하고,
#: metric 쿼리는 함수/집계 식별자로 시작한다 (`sum by (x) (...)`, `rate(...)`).
_METRIC_QUERY_RE = re.compile(r"^[A-Za-z_]\w*\s*(\(|by\b|without\b)")

#: LogQL 라벨 이름으로 쓸 수 있는 형태. 아니면 `by (...)` 절을 붙이지 않는다.
_LABEL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _mask(text: str) -> str:
    """오류 메시지·details 로 나가는 문자열에서 자격증명을 지운다.

    `base_url` 에는 `http://user:pass@loki` 처럼 자격증명이 박혀 있을 수 있고, 그 값이
    예외 메시지·`test_connection().details`·`query_runs.error_message` 를 타고 화면과
    DB 로 샌다. 마스킹은 화면 표시 전과 LLM 전송 직전 두 곳에 걸리지만 **오류 경로는
    그 두 곳 어디도 지나지 않는다** — 그래서 여기서 한 번 더 건다.

    지연 import 는 다른 트랙 모듈이 없는 상태에서도 이 모듈이 import 되게 하기 위한
    것이다(계약 테스트 `test_all_app_modules_import`).
    """
    if not text:
        return text
    try:
        from app.masking.service import mask as _mask_text
    except ImportError:  # pragma: no cover - 마스킹 트랙 미구현 시 방어
        return text
    return _mask_text(text)


def _to_nanoseconds(moment: datetime) -> str:
    """Loki `start`/`end` 파라미터용 나노초 epoch 문자열."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return str(int(moment.timestamp()) * 1_000_000_000 + moment.microsecond * 1_000)


def _from_nanoseconds(raw: str | int | float) -> datetime:
    return datetime.fromtimestamp(int(raw) / 1_000_000_000, tz=UTC)


def _from_seconds(raw: str | int | float) -> datetime:
    return datetime.fromtimestamp(float(raw), tz=UTC)


def is_metric_query(query: str) -> bool:
    """이미 metric 쿼리인지 (감싸면 안 되는지) 판정한다."""
    stripped = query.strip()
    if not stripped or stripped.startswith("{"):
        return False
    return bool(_METRIC_QUERY_RE.match(stripped))


def wrap_count_over_time(query: str, step: int, service_label: str = "service") -> str:
    """로그 쿼리를 건수 metric 쿼리로 감싼다. 이미 metric 쿼리면 그대로 둔다.

    `sum by (<service 라벨>)` 로 감싸는 것이 핵심이다. 라벨 없는 `sum(...)` 은 시리즈
    라벨을 **전부 떨어뜨려** 대시보드의 서비스별 건수가 영원히 비고, 조용히 DB(잘린
    라인) 집계로 폴백한다 — 정확히 metric 쿼리를 쓰기로 한 이유를 무효로 만든다.

    라벨 이름은 연결 설정의 `label_mapping`(`resolve_label_mapping`)에서 온다.
    LogQL 식별자로 쓸 수 없는 이름이면 `by (...)` 절 없이 감싼다.
    """
    stripped = query.strip()
    if is_metric_query(stripped):
        return stripped
    body = f"count_over_time({stripped} [{int(step)}s])"
    if service_label and _LABEL_NAME_RE.match(service_label):
        return f"sum by ({service_label}) ({body})"
    return f"sum({body})"


def escape_label_value_regex(name: str) -> str:
    """서비스 이름 하나를 `=~"..."` 안에 넣을 수 있는 형태로 두 겹 이스케이프한다.

    층이 두 개라서 한 번으로는 부족하다.

    1. **정규식 층** — `=~` 는 RE2 정규식이라 `.` `+` `(` 같은 문자가 메타로 먹는다.
       `re.escape` 로 리터럴화한다 (Loki 의 `=~` 는 완전 앵커라 부분 일치가 되지 않는다).
    2. **LogQL 문자열 층** — 셀렉터 값은 Go 스타일 따옴표 문자열이다. 1 번이 만든
       백슬래시를 그대로 두면 `\\.` 가 문자열 해제 단계에서 "알 수 없는 이스케이프"로
       **쿼리 자체가 파싱 실패**한다. 그래서 백슬래시를 한 번 더 늘리고, 이름에 박힌
       따옴표는 문자열을 끊지 못하게 escape 한다.
    """
    return re.escape(name).replace("\\", "\\\\").replace('"', '\\"')


def resolve_label_mapping(label_mapping: Mapping[str, str] | None) -> dict[str, str]:
    """표준 필드 -> 소스 라벨명 매핑을 확정한다. 없는 항목의 기본값은 동일 이름이다.

    freeze 문서 두 곳의 매핑 방향 설명이 서로 반대라(`models.py` 는
    `{"service": "app"}`, `schemas/api.py` 는 `{"app": "service"}`) 양쪽을 모두 받는다.
    표준 필드가 **키** 로 있으면 그 값을 소스 라벨로 쓰고, 없으면 표준 필드를
    **값** 으로 가리키는 키를 소스 라벨로 쓴다. 둘 다 없으면 동일 이름.
    """
    mapping = dict(label_mapping or {})
    resolved: dict[str, str] = {}
    for field in STANDARD_FIELDS:
        source_label = mapping.get(field)
        if not source_label:
            source_label = next(
                (key for key, value in mapping.items() if value == field and key), None
            )
        resolved[field] = source_label or field
    return resolved


class LokiProvider(LogSourceProvider):
    """Loki HTTP API 어댑터 (읽기 전용).

    복호화된 `secret` 을 받는 것은 호출자의 책임이다 (`app.loki.factory.build_provider`).
    `auth_type` 별 `secret` 형식:

    - `none`   : `None`
    - `basic`  : `"user:pass"`
    - `bearer` : 토큰 문자열
    - `header` : `"헤더이름: 값"` (예: 멀티테넌트 `X-Scope-OrgID: team-a`)
    """

    supports_count = True
    supports_label_discovery = True
    #: 수집 중단 확인(`service_presence`)을 metric 쿼리 1 회로 할 수 있다.
    supports_presence = True
    source_type = "loki"

    def __init__(
        self,
        base_url: str,
        auth_type: str,
        secret: str | None,
        label_mapping: dict[str, str],
        timeout_seconds: float = 15.0,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("base_url 이 비어 있습니다.")
        self.base_url = base_url.strip().rstrip("/")
        self.auth_type = str(auth_type or "none").strip().lower()
        self.secret = secret
        self.label_mapping = dict(label_mapping or {})
        self.timeout_seconds = float(timeout_seconds)
        self._standard_labels = resolve_label_mapping(self.label_mapping)

    @property
    def service_label(self) -> str:
        """metric 시리즈에서 서비스를 가리키는 **소스 라벨 이름**.

        `sum by (<이 이름>)` 으로 감싸므로, 호출 측(대시보드)은 이 이름으로 시리즈
        라벨을 읽어야 한다. 표준 필드 이름(`service`)과 다를 수 있다.
        """
        return self._standard_labels["service"]

    # ------------------------------------------------------------------ HTTP

    def _auth_and_headers(self) -> tuple[httpx.Auth | None, dict[str, str]]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.auth_type in ("", "none"):
            return None, headers
        if self.auth_type == "basic":
            if not self.secret:
                raise LogSourceError("basic 인증에는 'user:pass' 형식의 secret 이 필요합니다.")
            username, separator, password = self.secret.partition(":")
            if not separator:
                raise LogSourceError("basic 인증 secret 은 'user:pass' 형식이어야 합니다.")
            return httpx.BasicAuth(username, password), headers
        if self.auth_type == "bearer":
            if not self.secret:
                raise LogSourceError("bearer 인증에는 토큰 secret 이 필요합니다.")
            headers["Authorization"] = f"Bearer {self.secret}"
            return None, headers
        if self.auth_type == "header":
            if not self.secret or ":" not in self.secret:
                raise LogSourceError("header 인증 secret 은 '헤더이름: 값' 형식이어야 합니다.")
            name, _, value = self.secret.partition(":")
            headers[name.strip()] = value.strip()
            return None, headers
        raise LogSourceError(f"지원하지 않는 auth_type 입니다: {self.auth_type!r}")

    @contextmanager
    def _client(self) -> Iterator[httpx.Client]:
        auth, headers = self._auth_and_headers()
        client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            auth=auth,
            headers=headers,
        )
        try:
            yield client
        finally:
            client.close()

    def _request(
        self, client: httpx.Client, path: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        try:
            return client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise LogSourceError(
                _mask(
                    f"Loki 응답 시간을 초과했습니다 ({self.timeout_seconds:g}s): "
                    f"{self.base_url}{path}"
                )
            ) from exc
        except httpx.HTTPError as exc:
            # httpx 예외 메시지에는 요청 URL 이 그대로 들어간다 (자격증명 포함).
            raise LogSourceError(_mask(f"Loki 요청에 실패했습니다: {exc}")) from exc

    @staticmethod
    def _describe_failure(response: httpx.Response) -> str:
        body = (response.text or "").strip().replace("\n", " ")
        if len(body) > 200:
            body = body[:200] + "…"
        if response.status_code in (401, 403):
            prefix = "Loki 인증에 실패했습니다"
        else:
            prefix = "Loki 가 오류를 반환했습니다"
        # 응답 본문에는 요청 URL·헤더가 되비쳐 올 수 있다.
        return _mask(f"{prefix} (HTTP {response.status_code}){f': {body}' if body else ''}")

    def _json(self, client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> dict:
        response = self._request(client, path, params)
        if response.status_code >= 400:
            raise LogSourceError(
                self._describe_failure(response), status_code=response.status_code
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise LogSourceError(
                f"Loki 응답이 JSON 이 아닙니다 ({path}).", status_code=response.status_code
            ) from exc
        if not isinstance(payload, dict):
            raise LogSourceError(f"Loki 응답 형식이 예상과 다릅니다 ({path}).")
        return payload

    # ------------------------------------------------------- LogSourceProvider

    def test_connection(self) -> ConnectionTestResult:
        """`GET /ready` + `GET /loki/api/v1/labels` 로 연결·인증을 확인한다.

        `/ready` 는 프록시 뒤에서 막혀 있을 수 있으므로 실패해도 곧바로 실패로
        보지 않고 `details.ready` 로만 알린다 — 인증까지 확인해 주는 것은 labels 다.
        """
        # base_url 에 자격증명이 박혀 있을 수 있다 — details 는 화면으로 그대로 나간다.
        details: dict[str, Any] = {
            "base_url": _mask(self.base_url),
            "auth_type": self.auth_type,
        }
        started = perf_counter()
        try:
            with self._client() as client:
                ready = self._request(client, READY_PATH)
                details["ready"] = ready.status_code == 200
                details["ready_status"] = ready.status_code

                labels_response = self._request(client, LABELS_PATH)
                if labels_response.status_code >= 400:
                    return ConnectionTestResult(
                        ok=False,
                        message=self._describe_failure(labels_response),
                        latency_ms=int((perf_counter() - started) * 1000),
                        details=details,
                    )
                try:
                    payload = labels_response.json()
                except ValueError:
                    return ConnectionTestResult(
                        ok=False,
                        message="Loki labels 응답이 JSON 이 아닙니다.",
                        latency_ms=int((perf_counter() - started) * 1000),
                        details=details,
                    )
        except LogSourceError as exc:
            return ConnectionTestResult(
                ok=False,
                message=str(exc),
                latency_ms=int((perf_counter() - started) * 1000),
                details=details,
            )

        latency_ms = int((perf_counter() - started) * 1000)
        labels = [str(name) for name in (payload.get("data") or []) if name]
        details["label_count"] = len(labels)
        return ConnectionTestResult(
            ok=True,
            message=f"Loki 연결에 성공했습니다 (라벨 {len(labels)} 개).",
            latency_ms=latency_ms,
            details=details,
        )

    def list_labels(self) -> dict[str, list[str]]:
        """라벨 목록과 각 라벨의 값을 함께 돌려준다.

        개별 라벨의 값 조회가 실패해도 전체를 실패시키지 않는다 (빈 리스트).
        """
        with self._client() as client:
            payload = self._json(client, LABELS_PATH)
            names = [str(name) for name in (payload.get("data") or []) if name]
            result: dict[str, list[str]] = {}
            for name in names:
                if name.startswith("__"):
                    # Loki 내부 라벨(`__error__` 등)은 정책 작성 UI 에 노출하지 않는다.
                    continue
                try:
                    values_payload = self._json(client, LABEL_VALUES_PATH.format(name=name))
                except LogSourceError:
                    result[name] = []
                    continue
                result[name] = [str(value) for value in (values_payload.get("data") or [])]
        return result

    def fetch_logs(self, query: str, range: TimeRange, limit: int) -> FetchResult:  # noqa: A002
        """`GET /loki/api/v1/query_range` (direction=backward) 로 로그 라인을 읽는다."""
        requested_limit = max(1, int(limit))
        effective_limit = min(requested_limit, LOKI_MAX_ENTRIES)

        params = {
            "query": query,
            "start": _to_nanoseconds(range.start),
            "end": _to_nanoseconds(range.end),
            "limit": effective_limit,
            "direction": "backward",
        }
        with self._client() as client:
            payload = self._json(client, QUERY_RANGE_PATH, params)

        data = payload.get("data") or {}
        result_type = data.get("resultType", "streams")
        streams = data.get("result") or []
        if result_type != "streams":
            raise LogSourceError(
                f"로그 조회에 metric 쿼리가 사용되었습니다 (resultType={result_type!r}). "
                "건수·추이는 count_over_time() 을 쓰세요."
            )

        records: list[LogRecord] = []
        dropped = 0
        total_lines = 0
        error_reasons: set[str] = set()

        for stream in streams:
            labels = {
                str(key): str(value) for key, value in (stream.get("stream") or {}).items()
            }
            entries = stream.get("values") or []
            total_lines += len(entries)

            if labels.get(ERROR_LABEL):
                # `| json` 등 파서 스테이지가 실패시킨 줄. 레코드로 올리지 않는다.
                dropped += len(entries)
                error_reasons.add(labels[ERROR_LABEL])
                continue

            for entry in entries:
                if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                    dropped += 1
                    error_reasons.add("malformed_entry")
                    continue
                try:
                    timestamp = _from_nanoseconds(entry[0])
                except (TypeError, ValueError):
                    dropped += 1
                    error_reasons.add("malformed_timestamp")
                    continue
                records.append(self._to_record(timestamp, str(entry[1]), labels))

        warnings: list[FetchWarning] = []
        if dropped:
            reasons = ", ".join(sorted(error_reasons)) or "unknown"
            warnings.append(
                FetchWarning(
                    code="parse_error",
                    message=(
                        f"파싱 실패로 {dropped} 줄이 제외되었습니다 ({reasons}). "
                        "LogQL 에 `| __error__=\"\"` 를 넣으면 명시적으로 처리할 수 있습니다."
                    ),
                    count=dropped,
                )
            )

        truncated = total_lines >= effective_limit
        if truncated:
            if requested_limit > effective_limit:
                detail = (
                    f"요청 limit {requested_limit} 이 Loki 한도 {LOKI_MAX_ENTRIES} 로 잘렸습니다."
                )
            else:
                detail = f"limit {effective_limit} 에 도달했습니다."
            warnings.append(
                FetchWarning(
                    code="limit_reached",
                    message=f"{detail} 결과가 잘렸으므로 건수 집계에 쓰면 안 됩니다.",
                    count=total_lines,
                )
            )

        return FetchResult(
            records=records,
            fetched=total_lines,
            dropped=dropped,
            warnings=warnings,
            truncated=truncated,
        )

    def count_over_time(self, query: str, range: TimeRange, step: int) -> CountSeries:  # noqa: A002
        """건수·추이 metric 조회. 로그 쿼리는 `count_over_time` 으로 감싼다."""
        step_seconds = max(1, int(step))
        metric_query = wrap_count_over_time(
            query, step_seconds, self._standard_labels["service"]
        )

        params = {
            "query": metric_query,
            "start": _to_nanoseconds(range.start),
            "end": _to_nanoseconds(range.end),
            "step": step_seconds,
        }
        with self._client() as client:
            payload = self._json(client, QUERY_RANGE_PATH, params)

        data = payload.get("data") or {}
        result_type = data.get("resultType", "matrix")
        series = data.get("result") or []

        points: list[CountPoint] = []
        warnings: list[FetchWarning] = []

        if result_type == "streams":
            raise LogSourceError(
                _mask(
                    "count_over_time 결과가 로그 스트림입니다. metric 쿼리가 아닙니다: "
                    f"{metric_query}"
                )
            )

        for entry in series:
            labels = {str(key): str(value) for key, value in (entry.get("metric") or {}).items()}
            if result_type == "vector":
                raw_points = [entry.get("value")] if entry.get("value") else []
            else:
                raw_points = entry.get("values") or []
            for raw in raw_points:
                if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                    continue
                try:
                    points.append(
                        CountPoint(
                            timestamp=_from_seconds(raw[0]),
                            value=float(raw[1]),
                            labels=labels,
                        )
                    )
                except (TypeError, ValueError):
                    continue

        points.sort(key=lambda point: (point.timestamp, sorted(point.labels.items())))
        if not points:
            warnings.append(
                FetchWarning(code="empty_result", message="metric 쿼리 결과가 비어 있습니다.")
            )
        return CountSeries(step_seconds=step_seconds, points=points, warnings=warnings)

    def service_presence(self, services: list[str], range: TimeRange) -> set[str]:  # noqa: A002
        """이 기간에 로그를 **한 줄이라도** 낸 서비스의 집합 (metric 쿼리 1 회).

        정책 쿼리를 쓰지 않는 것이 요점이다 — 정책은 보통 `level="ERROR"` 로 좁혀져
        있어서, 그 쿼리로는 "오류가 없는 정상 서비스" 와 "로그가 아예 끊긴 서비스" 가
        똑같이 0 으로 보인다. 여기서는 라벨 셀렉터만으로 전체 로그를 센다.

        step 은 기간 전체를 한 버킷으로 잡는다. 필요한 것은 추이가 아니라 "있었나"
        하나뿐이라, 버킷을 잘게 나눌수록 응답만 커진다.
        """
        names: list[str] = []
        seen: set[str] = set()
        for value in services or ():
            name = str(value).strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        if not names:
            return set()

        label = self._standard_labels["service"]
        if not _LABEL_NAME_RE.match(label):
            raise LogSourceError(
                f"서비스 라벨 이름을 LogQL 셀렉터에 쓸 수 없습니다: {label!r}"
            )

        pattern = "|".join(escape_label_value_regex(name) for name in names)
        selector = f'{{{label}=~"{pattern}"}}'
        # 기간 전체를 1 버킷으로. `count_over_time` 이 자기 라벨(`sum by (label)`)을
        # 유지하므로 돌아온 시리즈의 라벨이 곧 "관측된 서비스" 다.
        step = max(1, int((range.end - range.start).total_seconds()))
        series = self.count_over_time(selector, range, step)

        present: set[str] = set()
        for point in series.points:
            if point.value <= 0:
                continue
            observed = point.labels.get(label)
            if observed:
                present.add(observed)
        return present

    # ------------------------------------------------------------- 정규화

    def _to_record(self, timestamp: datetime, message: str, labels: dict[str, str]) -> LogRecord:
        """Loki 스트림 라벨을 표준 필드까지 채운 `LogRecord` 로 정규화한다."""
        return LogRecord(
            timestamp=timestamp,
            message=message,
            labels=labels,
            service=labels.get(self._standard_labels["service"]),
            environment=labels.get(self._standard_labels["environment"]),
            level=labels.get(self._standard_labels["level"]),
        )


__all__ = [
    "ERROR_DETAILS_LABEL",
    "ERROR_LABEL",
    "LOKI_MAX_ENTRIES",
    "LokiProvider",
    "escape_label_value_regex",
    "is_metric_query",
    "resolve_label_mapping",
    "wrap_count_over_time",
]
