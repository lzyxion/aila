#!/usr/bin/env python
"""AILA E2E 데모 시나리오 — Compose 스택 하나로 전체 경로를 끝까지 돌린다.

무엇을 증명하는가
-----------------
MVP 성공 기준은 "Docker Compose 만으로 데모 환경과 장애 시나리오를 재현할 수 있다"이다.
이 스크립트는 그 문장을 **실행 가능한 단언**으로 바꾼 것이다.

    로그인 → 연결 등록 → 시나리오 트리거 → 정책 조회 → 그룹화 → 마스킹 → LLM 분석 → 보고서

중에서 되돌릴 수 없는 위험 하나(민감정보 유출)는 두 지점에서 따로 단언한다.

- **화면 경로**: 그룹 상세의 `masked_log` 에 `<MASKED:` 가 있고 원문 비밀값이 없다.
- **전송 경로**: llm-mock 의 `/debug/last-request` 로 받은 **실제 요청 본문**에
  원문 비밀값이 없다. 설계 문서가 요구하는 "LLM 요청 페이로드에 원문 토큰이 없음을
  단언하는 자동 테스트"가 이것이고, 진짜 프로바이더로는 관측할 수 없어서
  `infra/llm-mock` 이 존재한다.

두 번째가 핵심이다. 화면만 검사하면 프롬프트 조립이 다른 경로(라벨·정규화 메시지·
스택 프레임)로 원문을 실어 보내는 사고를 잡지 못한다.

실행
----
    docker compose --profile app up -d --build
    docker compose --profile app exec backend alembic upgrade head
    backend\\.venv\\Scripts\\python.exe scripts\\e2e_demo.py

httpx 만 있으면 되므로 백엔드 가상환경의 python 으로 돌리는 게 가장 간단하다.
(다른 python 을 쓰려면 `pip install httpx`.)

Phase 5 부터 `/api/**` 는 세션 쿠키 인증이 필요하다 — 사전 점검 단계에서 먼저
로그인한다 (기본 `admin/admin`, 바꿨으면 `--username/--password`).
llm-mock·Loki·demo-api 는 인증 대상이 아니다.

멱등하다 — 같은 이름의 연결·정책이 있으면 새로 만들지 않고 재사용한다. 몇 번을 다시
돌려도 된다. 다만 분석은 **매번 실제 호출**이므로 일일 분석 한도를 소모한다.

실패하면 어느 단계에서 무엇이 어긋났는지 출력하고 비0 으로 종료한다.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - 사용 안내
    print(
        "httpx 가 필요합니다. 백엔드 가상환경으로 실행하거나 `pip install httpx` 하십시오.\n"
        "  backend\\.venv\\Scripts\\python.exe scripts\\e2e_demo.py",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

REPO_ROOT = Path(__file__).resolve().parent.parent

# Windows 콘솔 기본 인코딩이 cp949 라 한국어 출력이 그대로면 UnicodeEncodeError 로 죽는다.
# (backend/alembic.ini 를 ASCII 로 유지하는 것과 같은 이유의 환경 제약이다.)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError):  # pragma: no cover - 리다이렉트된 스트림 등
        pass

# --------------------------------------------------------------------- 기본값

DEFAULT_API = "http://localhost:8000"
DEFAULT_DEMO = "http://localhost:8081"
DEFAULT_LLM_DEBUG = "http://localhost:8090"
#: 백엔드는 컨테이너 안에서 돈다 — 백엔드가 보는 주소를 등록해야 한다.
DEFAULT_LOKI_URL = "http://loki:3100"
DEFAULT_LLM_BASE_URL = "http://llm-mock:8000/v1"
DEFAULT_LLM_MODEL = "llm-mock-1"
#: Phase 5 부터 `/api/**` 는 세션 쿠키 인증이 필요하다. compose 기본 관리자 계정.
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"

LOKI_CONNECTION_NAME = "demo-loki"
LLM_CONNECTION_NAME = "demo-llm-mock"
ERROR_POLICY_NAME = "demo · payment-api ERROR"
SECRET_POLICY_NAME = "demo · secret-leak (마스킹 검증)"

#: payment-api 의 ERROR 만 (정형 로그). `| json` 이 실패한 줄은 여기서 걸러진다.
ERROR_LOGQL = '{service="payment-api"} | json | level="ERROR"'
#: 시나리오 06 만. 라인 필터라 파서 실패에 영향받지 않는다.
SECRET_LOGQL = '{service="payment-api"} |= "PaymentRetryError"'

MASK_MARK = "<MASKED:"
REPORT_MARK = "LLM 이 생성한 원인 가설"


# --------------------------------------------------------------------- 출력


class Fail(Exception):
    """단계 실패. 메시지에 무엇이 어긋났는지 담는다."""


_step_no = 0


def step(title: str) -> None:
    global _step_no
    _step_no += 1
    print(f"\n[{_step_no:02d}] {title}")
    print("-" * (len(title) + 7))


def info(message: str) -> None:
    print(f"     {message}")


def ok(message: str) -> None:
    print(f"  OK  {message}")


def check(condition: object, message: str, detail: str = "") -> None:
    if condition:
        ok(message)
        return
    raise Fail(message + (f"\n      {detail}" if detail else ""))


def _short(value: Any, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + " …"


# ------------------------------------------------------- 데모 비밀값(가짜) 수집


#: `infra/demo-api/app/scenarios.py` 를 읽지 못할 때의 보루. 값이 갈라지면 감사가
#: 조용히 약해지므로 원본 파일에서 뽑는 것을 기본 경로로 둔다.
FALLBACK_SECRETS: dict[str, str] = {
    "FAKE_JWT": (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".ZmFrZS1kZW1vLXBheWxvYWQtbm90LWEtcmVhbC10b2tlbg"
        ".FAKE0SIGNATURE0FOR0AILA0MASKING0DEMO0ONLY"
    ),
    "FAKE_EMAIL": "demo.user@example.com",
    "FAKE_EMAIL_ALT": "ops-oncall@example.org",
    "FAKE_PHONE": "010-0000-0000",
    "FAKE_CARD": "4111-1111-1111-1111",
    "FAKE_API_KEY": "sk-demo-FAKE000000000000000000000000000000",
    "FAKE_DB_URL": "postgresql://demo_user:not-a-real-password@db.example.invalid:5432/demo",
    "FAKE_COOKIE": "session=FAKE-SESSION-COOKIE-VALUE-DEMO-ONLY; Path=/; HttpOnly",
}


def load_demo_secrets() -> dict[str, str]:
    """시나리오 06 이 심는 가짜 비밀값 원문을 demo-api 소스에서 그대로 읽는다.

    복사해 두면 demo-api 가 값을 바꿨을 때 감사가 **조용히** 아무것도 검사하지 않게
    된다. 그래서 소스의 `FAKE_*` 상수를 AST 로 뽑는다 (import 는 상대 import 때문에
    불가능하다).
    """
    source_path = REPO_ROOT / "infra" / "demo-api" / "app" / "scenarios.py"
    secrets: dict[str, str] = {}
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
    except OSError:
        info(f"! {source_path} 를 읽지 못해 내장 목록을 씁니다.")
        secrets = dict(FALLBACK_SECRETS)
    else:
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name) or not target.id.startswith("FAKE_"):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                secrets[target.id] = node.value.value
        if not secrets:
            info("! scenarios.py 에서 FAKE_* 를 찾지 못해 내장 목록을 씁니다.")
            secrets = dict(FALLBACK_SECRETS)

    # f-string 이라 AST 에서 값이 안 나오는 것 + 부분 문자열 needle 을 보탠다.
    jwt = secrets.get("FAKE_JWT", FALLBACK_SECRETS["FAKE_JWT"])
    secrets.setdefault("FAKE_BEARER", f"Bearer {jwt}")
    db_url = secrets.get("FAKE_DB_URL", FALLBACK_SECRETS["FAKE_DB_URL"])
    if ":" in db_url and "@" in db_url:
        password = db_url.rsplit("@", 1)[0].rsplit(":", 1)[-1]
        if password:
            secrets.setdefault("FAKE_DB_PASSWORD", password)
    cookie = secrets.get("FAKE_COOKIE", FALLBACK_SECRETS["FAKE_COOKIE"])
    cookie_value = cookie.split(";", 1)[0].split("=", 1)[-1]
    if cookie_value:
        secrets.setdefault("FAKE_COOKIE_VALUE", cookie_value)
    return secrets


def find_leaks(haystack: str, secrets: dict[str, str]) -> list[str]:
    """원문 비밀값이 남아 있으면 그 이름들을 돌려준다."""
    return [name for name, value in secrets.items() if value and value in haystack]


# ---------------------------------------------------------------- HTTP 래퍼


class Api:
    """백엔드 REST 래퍼. 상태 코드가 어긋나면 본문까지 담아 실패시킨다."""

    def __init__(self, base: str, timeout: float = 120.0) -> None:
        self.client = httpx.Client(base_url=base.rstrip("/"), timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def request(
        self, method: str, path: str, *, expect: tuple[int, ...] = (200,), **kwargs: Any
    ) -> httpx.Response:
        response = self.client.request(method, path, **kwargs)
        if response.status_code not in expect:
            raise Fail(
                f"{method} {path} → HTTP {response.status_code} "
                f"(기대: {', '.join(str(code) for code in expect)})\n"
                f"      {_short(response.text, 600)}"
            )
        return response

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs).json()

    def post(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        response = self.request("POST", path, json=body, **kwargs)
        return response.json() if response.content else None

    def patch(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        return self.request("PATCH", path, json=body, **kwargs).json()

    def login(self, username: str, password: str) -> dict:
        """세션 쿠키를 받아 이후 요청에 자동으로 싣는다 (`httpx.Client` 가 보관한다)."""
        return self.post(
            "/api/auth/login", {"username": username, "password": password}, expect=(200,)
        )


def wait_for(
    label: str, probe: Any, *, timeout: float = 60.0, interval: float = 2.0
) -> Any:
    """`probe()` 가 참 같은 값을 돌려줄 때까지 기다린다."""
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = probe()
        except Exception as exc:  # noqa: BLE001 - 기동 중 예외는 재시도 대상이다
            last = exc
        else:
            if last:
                return last
        time.sleep(interval)
    raise Fail(f"{label} 대기 시간({timeout:.0f}s)을 넘겼습니다. 마지막 결과: {_short(repr(last))}")


# ---------------------------------------------------------------- 각 단계


def step_preflight(api: Api, args: argparse.Namespace) -> None:
    step("사전 점검 — 백엔드·demo-api·llm-mock 이 떠 있는가")

    health = wait_for("backend /health", lambda: api.get("/health"), timeout=90)
    check(health.get("status") == "ok", f"backend /health = {health}")

    with httpx.Client(timeout=15.0) as client:
        demo = wait_for(
            "demo-api /health",
            lambda: client.get(f"{args.demo}/health").json(),
            timeout=60,
        )
        check(demo.get("status") in ("ok", "healthy", None) or demo, f"demo-api /health = {demo}")

        mock = wait_for(
            "llm-mock /health",
            lambda: client.get(f"{args.llm_debug}/health").json(),
            timeout=60,
        )
        check(mock.get("status") == "ok", f"llm-mock /health = {mock}")

        # 감사 대상을 이번 실행분으로 한정한다 (재실행 시 이전 프롬프트가 섞이지 않게).
        cleared = client.post(f"{args.llm_debug}/debug/reset").json()
        info(f"llm-mock 요청 이력 초기화: {cleared}")

    # Phase 5: `/api/**` 는 세션 쿠키 인증이 필요하다. 로그인부터 한다.
    # (llm-mock·Loki·demo-api 는 인증 대상이 아니라 위 검사에는 로그인이 필요 없다.)
    try:
        me = api.login(args.username, args.password)
    except Fail as exc:
        raise Fail(
            "로그인에 실패했습니다. 관리자 계정을 확인하십시오 "
            "(compose 기본값 admin/admin, env AILA_ADMIN_USERNAME/AILA_ADMIN_PASSWORD).\n"
            "      users 테이블이 없다면 마이그레이션이 먼저입니다:\n"
            "      docker compose --profile app exec backend alembic upgrade head\n"
            f"      원인: {exc}"
        ) from exc
    check(me.get("role") == "admin", f"로그인 성공: {me}")
    if args.username == DEFAULT_USERNAME and args.password == DEFAULT_PASSWORD:
        info("기본 관리자 계정(admin/admin)으로 로그인했습니다 — 데모 밖에서는 반드시 교체하십시오.")

    # 마이그레이션이 안 돌았으면 여기서 명확히 죽는 편이 낫다.
    try:
        api.get("/api/loki-connections")
    except Fail as exc:
        raise Fail(
            "백엔드가 DB 를 읽지 못합니다. 마이그레이션을 먼저 적용하십시오.\n"
            "      docker compose --profile app exec backend alembic upgrade head\n"
            f"      원인: {exc}"
        ) from exc
    ok("DB 스키마 적용 확인 (/api/loki-connections 응답)")


def step_loki_connection(api: Api, args: argparse.Namespace) -> int:
    step("a. Loki 연결 등록 + 연결 테스트")

    existing = api.get("/api/loki-connections")
    match = next((item for item in existing if item["name"] == LOKI_CONNECTION_NAME), None)

    if match is None:
        created = api.post(
            "/api/loki-connections",
            {
                "name": LOKI_CONNECTION_NAME,
                "source_type": "loki",
                "base_url": args.loki_url,
                "auth_type": "none",
                "label_mapping": {},
                "active": True,
            },
            expect=(201,),
        )
        connection_id = int(created["id"])
        ok(f"연결 생성 id={connection_id} base_url={args.loki_url}")
    else:
        connection_id = int(match["id"])
        if match["base_url"] != args.loki_url or not match["active"]:
            api.patch(
                f"/api/loki-connections/{connection_id}",
                {"base_url": args.loki_url, "active": True},
            )
            ok(f"기존 연결 id={connection_id} 갱신 (base_url={args.loki_url})")
        else:
            ok(f"기존 연결 재사용 id={connection_id}")

    result = api.post("/api/loki-connections/test", {"connection_id": connection_id})
    check(
        result.get("ok") is True,
        f"연결 테스트 ok=True ({result.get('latency_ms')}ms) — {result.get('message', '')}",
        detail=_short(result),
    )

    labels = api.get(f"/api/loki-connections/{connection_id}/labels")
    check(
        "service" in labels.get("labels", []),
        f"라벨 탐색에 service 존재 (총 {len(labels.get('labels', []))} 개)",
        detail=_short(labels),
    )
    return connection_id


def step_llm_connection(api: Api, args: argparse.Namespace) -> int:
    step("b. LLM 연결 등록(openai_compatible → llm-mock) + 연결 테스트")

    payload = {
        "name": LLM_CONNECTION_NAME,
        "provider": "openai_compatible",
        "model": args.llm_model,
        "base_url": args.llm_base_url,
        "is_default": True,
        "active": True,
        # llm-mock 은 인증이 없다. 그래도 키를 넣어 Fernet 암복호화 경로를 함께 태운다.
        "api_key": "mock-key-not-a-real-credential",
    }

    existing = api.get("/api/llm-connections")
    match = next((item for item in existing if item["name"] == LLM_CONNECTION_NAME), None)

    if match is None:
        created = api.post("/api/llm-connections", payload, expect=(201,))
        connection_id = int(created["id"])
        ok(f"연결 생성 id={connection_id} model={args.llm_model} base_url={args.llm_base_url}")
        masked = created.get("api_key_masked")
    else:
        connection_id = int(match["id"])
        updated = api.patch(
            f"/api/llm-connections/{connection_id}",
            {
                "provider": "openai_compatible",
                "model": args.llm_model,
                "base_url": args.llm_base_url,
                "is_default": True,
                "active": True,
                "api_key": payload["api_key"],
            },
        )
        ok(f"기존 연결 재사용/갱신 id={connection_id}")
        masked = updated.get("api_key_masked")

    check(
        masked is not None and payload["api_key"] not in str(masked),
        f"API 키가 평문으로 응답에 실리지 않음 (api_key_masked={masked})",
    )

    result = api.post("/api/llm-connections/test", {"connection_id": connection_id})
    check(
        result.get("ok") is True,
        f"연결 테스트 ok=True ({result.get('latency_ms')}ms) — {result.get('message', '')}",
        detail=_short(result),
    )
    return connection_id


def step_trigger_scenarios(args: argparse.Namespace) -> None:
    step("c. demo-api 장애 시나리오 트리거 (06 비밀값 포함, burst 포함)")

    calls: list[tuple[str, str, dict[str, Any]]] = [
        ("GET", "/payment/charge", {}),  # 01
        ("GET", "/orders", {}),  # 02
        ("GET", "/auth/me", {}),  # 03
        ("GET", "/profile", {}),  # 04
        ("POST", "/admin/deploy", {"params": {"release": "v1.5.0", "spike_seconds": "120"}}),  # 05
        ("GET", "/debug/dump-context", {}),  # 06 — 마스킹 검증의 입력
        ("GET", "/debug/dump-context", {}),
        ("GET", "/debug/dump-context", {}),
        ("POST", "/scenarios/payment-timeout/burst", {"params": {"count": "120"}}),
        ("POST", "/scenarios/noise", {"params": {"count": "20"}}),
    ]

    with httpx.Client(base_url=args.demo, timeout=60.0) as client:
        for method, path, kwargs in calls:
            response = client.request(method, path, **kwargs)
            # 시나리오 엔드포인트는 장애를 재현하므로 5xx 도 정상 동작이다.
            info(f"{method} {path} → HTTP {response.status_code} {_short(response.text, 120)}")

        # 배포 스파이크를 되돌려 다음 실행이 같은 상태에서 시작하게 한다 (멱등).
        client.post("/admin/deploy")

    ok(f"시나리오 6 종 + burst(120) + noise(20) 트리거 완료 ({len(calls)} 호출)")


def step_policy(
    api: Api,
    *,
    connection_id: int,
    name: str,
    logql: str,
    description: str,
) -> dict[str, Any]:
    existing = api.get("/api/policies")
    match = next((item for item in existing if item["name"] == name), None)
    body = {
        "loki_connection_id": connection_id,
        "name": name,
        "description": description,
        "logql": logql,
        "default_range_minutes": 60,
        "max_lines": 1000,
        "exclusions": [],
        "max_samples_per_group": 3,
        "allow_ai_analysis": True,
        "daily_analysis_limit": None,
    }
    if match is None:
        policy = api.post("/api/policies", body, expect=(201,))
        ok(f"정책 생성 id={policy['id']} · {name}")
    else:
        policy = api.patch(f"/api/policies/{match['id']}", {**body, "active": True})
        ok(f"정책 재사용/갱신 id={policy['id']} · {name}")
    info(f"logql = {logql}")
    return policy


def step_preview_and_run(
    api: Api, *, policy: dict[str, Any], connection_id: int, min_groups: int = 1
) -> dict[str, Any]:
    preview = wait_for(
        f"'{policy['name']}' 미리보기 결과 유입",
        lambda: (
            lambda result: result if result.get("fetched", 0) > 0 else None
        )(
            api.post(
                "/api/policies/preview",
                {
                    "loki_connection_id": connection_id,
                    "logql": policy["logql"],
                    "range_minutes": 60,
                    "limit": 50,
                    "exclusions": [],
                },
            )
        ),
        timeout=120,
        interval=3,
    )
    check(
        preview["fetched"] > 0,
        f"미리보기 fetched={preview['fetched']} dropped={preview['dropped']} "
        f"warnings={[w.get('code') for w in preview.get('warnings', [])]}",
    )
    sample = (preview.get("sample_lines") or [""])[0]
    info(f"sample_lines[0] = {_short(sample, 200)}")

    run = api.post(f"/api/policies/{policy['id']}/query-runs", {}, expect=(201,))
    check(
        run["status"] == "succeeded",
        f"query-run id={run['id']} status={run['status']} "
        f"fetched={run['fetched_count']} dropped={run['dropped_count']}",
        detail=_short(run.get("error_message") or run),
    )
    check(
        run["group_count"] >= min_groups,
        f"오류 그룹 {run['group_count']} 개 (>= {min_groups})",
        detail=_short(run),
    )
    if run.get("warnings"):
        info(f"warnings = {[w.get('code') for w in run['warnings']]}")
    return run


def step_masking_on_screen(
    api: Api, *, run: dict[str, Any], secrets: dict[str, str]
) -> dict[str, Any]:
    step("e. 그룹 상세 마스킹 검증 — <MASKED: 존재 · 원문 비밀값 부재")

    listing = api.get(f"/api/query-runs/{run['id']}/error-groups")
    check(listing["total"] >= 1, f"그룹 목록 {listing['total']} 건")

    detail: dict[str, Any] | None = None
    for item in listing["items"]:
        candidate = api.get(f"/api/error-groups/{item['id']}")
        if any(MASK_MARK in (sample["masked_log"] or "") for sample in candidate["samples"]):
            detail = candidate
            break
    if detail is None:
        # 마스킹 표식이 하나도 없다면 그것 자체가 실패다 — 첫 그룹으로 이유를 보여준다.
        first = api.get(f"/api/error-groups/{listing['items'][0]['id']}")
        raise Fail(
            "어느 그룹에서도 <MASKED: 표식을 찾지 못했습니다 — 마스킹 경로가 끊겼습니다.\n"
            f"      {_short(first.get('samples'), 600)}"
        )

    ok(f"그룹 id={detail['id']} error_type={detail['error_type']} count={detail['count']}")
    info(f"normalized_message = {_short(detail['normalized_message'], 200)}")

    masked_logs = [sample["masked_log"] for sample in detail["samples"]]
    check(masked_logs, f"대표 로그 {len(masked_logs)} 개")
    check(
        all(MASK_MARK in log for log in masked_logs),
        f"모든 대표 로그에 {MASK_MARK} 표식이 있음",
    )
    kinds = sorted(set(re.findall(r"<MASKED:([A-Z_]+)>", "\n".join(masked_logs))))
    info(f"마스킹 종류 = {kinds}")
    info(f"masked_log[0] = {_short(masked_logs[0], 300)}")

    # 상세 응답 **전체**(대표 로그·라벨·스택·정규화 메시지)를 통째로 훑는다.
    blob = json.dumps(detail, ensure_ascii=False)
    leaks = find_leaks(blob, secrets)
    check(
        not leaks,
        f"그룹 상세 응답 어디에도 원문 비밀값이 없음 ({len(secrets)} 종 검사)",
        detail=f"누출된 상수: {leaks}",
    )
    return detail


def step_analyze(api: Api, *, group_id: int, label: str) -> dict[str, Any]:
    created = api.post(f"/api/error-groups/{group_id}/analysis-jobs", {}, expect=(202,))
    job_id = int(created["id"])
    ok(f"{label}: 분석 작업 생성 id={job_id} reused={created.get('reused')} "
       f"provider={created['provider']} model={created['model']}")

    def poll() -> dict[str, Any] | None:
        job = api.get(f"/api/analysis-jobs/{job_id}")
        return job if job["status"] in ("succeeded", "failed") else None

    job = wait_for(f"{label} 분석 완료", poll, timeout=180, interval=2)
    check(
        job["status"] == "succeeded",
        f"{label}: status=succeeded (prompt_version={job['prompt_version']})",
        detail=f"error_message={job.get('error_message')}",
    )

    result = job.get("result") or {}
    check(result.get("hypotheses"), f"{label}: hypotheses {len(result.get('hypotheses') or [])} 개")
    check(
        result.get("limitations"), f"{label}: limitations {len(result.get('limitations') or [])} 개"
    )
    info(f"summary = {_short(result.get('summary'), 200)}")
    info(f"severity = {result.get('severity')}")
    info(f"hypotheses[0].cause = {_short((result['hypotheses'][0] or {}).get('cause'), 160)}")
    info(f"limitations[0] = {_short(result['limitations'][0], 160)}")

    usage = job.get("usage") or {}
    check(
        (usage.get("input_tokens") or 0) > 0 and (usage.get("output_tokens") or 0) > 0,
        f"{label}: usage input={usage.get('input_tokens')} output={usage.get('output_tokens')} "
        f"latency={usage.get('latency_ms')}ms estimated_cost={usage.get('estimated_cost')}",
        detail=_short(usage),
    )
    return job


def step_payload_audit(args: argparse.Namespace, secrets: dict[str, str]) -> None:
    step("g. 전송 페이로드 감사 — llm-mock 이 실제로 받은 요청에 원문 비밀값이 없는가")

    with httpx.Client(timeout=30.0) as client:
        last = client.get(f"{args.llm_debug}/debug/last-request", params={"kind": "analysis"})
        check(
            last.status_code == 200,
            "llm-mock /debug/last-request?kind=analysis 응답 200",
            detail=_short(last.text),
        )
        last_body = last.json()
        history = client.get(
            f"{args.llm_debug}/debug/requests", params={"kind": "analysis", "limit": 50}
        ).json()

    prompt = last_body.get("prompt_text") or ""
    check(bool(prompt), f"마지막 분석 요청 프롬프트 {len(prompt)} 자 수신")
    check(
        MASK_MARK in prompt,
        f"프롬프트에 {MASK_MARK} 표식 존재 (전송 직전 마스킹이 걸렸다)",
        detail=_short(prompt, 800),
    )
    check(
        not last_body.get("has_authorization_header")
        or "mock-key-not-a-real-credential" not in json.dumps(last_body, ensure_ascii=False),
        "저장된 API 키가 요청 본문/감사 로그에 평문으로 남지 않음",
    )

    # 이번 실행에서 llm-mock 이 받은 **모든** 분석 요청 본문을 통째로 훑는다.
    blob = json.dumps(history, ensure_ascii=False)
    leaks = find_leaks(blob, secrets)
    check(
        not leaks,
        f"분석 요청 {history['returned']} 건 전체에 원문 비밀값이 없음 ({len(secrets)} 종 검사)",
        detail=f"누출된 상수: {leaks}",
    )
    info(f"llm-mock 수신 집계 = {history.get('total_received')}")

    excerpt = "\n".join(
        line for line in prompt.splitlines() if MASK_MARK in line
    )
    info("마스킹된 프롬프트 발췌:")
    for line in excerpt.splitlines()[:4]:
        print(f"       | {_short(line.strip(), 200)}")


def step_report_usage_dashboard(
    api: Api,
    *,
    jobs: list[dict[str, Any]],
    policy: dict[str, Any],
    secrets: dict[str, str],
) -> None:
    step("h. 보고서 · usage 집계 · 대시보드 overview")

    job = jobs[-1]
    response = api.request("GET", f"/api/analysis-jobs/{job['id']}/report")
    markdown = response.text
    check(
        REPORT_MARK in markdown,
        f'보고서에 "{REPORT_MARK}" 표기 포함 ({len(markdown)} 자)',
        detail=_short(markdown, 400),
    )
    check(
        response.headers.get("content-type", "").startswith("text/markdown"),
        f"Content-Type = {response.headers.get('content-type')}",
    )
    leaks = find_leaks(markdown, secrets)
    check(not leaks, "보고서에도 원문 비밀값이 없음", detail=f"누출된 상수: {leaks}")
    info("보고서 앞부분:")
    for line in markdown.splitlines()[:6]:
        print(f"       | {_short(line, 200)}")

    usage = api.get("/api/usage")
    check(
        usage["total_jobs"] >= len(jobs),
        f"usage total_jobs={usage['total_jobs']} input={usage['total_input_tokens']} "
        f"output={usage['total_output_tokens']} 추정비용={usage['total_estimated_cost']}",
        detail=_short(usage),
    )
    check(
        usage["total_input_tokens"] > 0 and usage["total_output_tokens"] > 0,
        "토큰 집계가 0 이 아님",
    )
    for item in usage["items"]:
        info(
            f"{item['provider']}/{item['model']}: jobs={item['job_count']} "
            f"실패={item['failure_count']} in={item['input_tokens']} out={item['output_tokens']} "
            f"평균지연={item['avg_latency_ms']}ms"
        )

    # 화면(기간 프리셋)이 부르는 방식 — range_end 를 '지금'으로 준다.
    #
    # Loki 는 query_range 의 step 을 **epoch 기준으로 정렬**하므로, 마지막 정렬 시각
    # 이후에 들어온 로그는 다음 정렬 시각이 range_end 를 넘기 전까지 metric 결과에
    # 나타나지 않는다. 방금 터뜨린 로그가 step 경계를 넘길 때까지 잠깐 기다린다.
    def overview_now() -> dict[str, Any] | None:
        now = datetime.now(UTC)
        result = api.get(
            "/api/dashboard/overview",
            params={
                "policy_id": policy["id"],
                "range_start": (now - timedelta(minutes=60)).isoformat(),
                "range_end": now.isoformat(),
                "step_seconds": 60,
            },
        )
        return result if result["total_errors"] > 0 else None

    overview = wait_for("대시보드 metric 추이", overview_now, timeout=150, interval=5)
    check(
        overview["total_errors"] > 0,
        f"대시보드 total_errors={overview['total_errors']:g} "
        f"(metric 쿼리 기준, series {len(overview['series'])} 점)",
        detail=_short(overview),
    )
    check(
        overview["top_groups"],
        f"상위 오류 그룹 {len(overview['top_groups'])} 건 · "
        f"서비스별 {[(s['service'], s['count']) for s in overview['by_service'][:3]]}",
    )

    # 기간을 주지 않으면 백엔드는 **마지막 조회의 range_end** 를 끝으로 쓴다. 그 시각은
    # 항상 부분 버킷 한가운데라, 예전에는 Loki 의 step 정렬에 걸려 마지막 버킷이 통째로
    # 빠지고 추이가 영구히 비었다 (by_service 는 값이 있는데 total_errors 만 0 인 상태).
    # 지금은 서버가 range_end 를 step 경계로 **올림**하므로 그 공백이 없어야 한다.
    scoped = api.get("/api/dashboard/overview", params={"policy_id": policy["id"]})
    if scoped["total_errors"] <= 0:
        info(
            "! 기간 미지정(policy_id 만) 호출이 total_errors=0 이다 — "
            f"by_service={[(s['service'], s['count']) for s in scoped['by_service'][:2]]}, "
            f"warnings={[w.get('code') for w in scoped['warnings']]}."
        )
    else:
        info(f"기간 미지정 호출 total_errors={scoped['total_errors']:g} (step 경계 올림 적용)")
    analyzed = [
        group for group in overview["top_groups"] if group.get("analysis_status") is not None
    ]
    info(f"상위 그룹 중 분석 상태가 붙은 것 {len(analyzed)} 건 (fingerprint 기준 조인)")

    # Phase 5 통합 대시보드 — 정책 전체를 한 줄씩. 정책 하나의 metric 실패가
    # 나머지 줄을 죽이지 않는지도 여기서 함께 확인된다 (실패 시 total_errors_24h=null).
    summary = api.get("/api/dashboard/summary")
    row = next(
        (item for item in summary["policies"] if item["policy_id"] == policy["id"]), None
    )
    check(row is not None, f"통합 대시보드에 정책 {len(summary['policies'])} 건", detail=_short(summary))
    check(
        row["last_run"] is not None,
        f"정책 '{row['name']}': last_run={row['last_run'] and row['last_run']['status']} "
        f"미분석 그룹={row['unanalyzed_group_count']} "
        f"24h 오류={row['total_errors_24h']} 스케줄={row['schedule_enabled']}",
        detail=_short(row),
    )


# ------------------------------------------------------------------- 진입점


def main() -> int:
    parser = argparse.ArgumentParser(description="AILA E2E 데모 시나리오")
    parser.add_argument("--api", default=DEFAULT_API, help="백엔드 base URL (호스트 관점)")
    parser.add_argument("--demo", default=DEFAULT_DEMO, help="demo-api base URL (호스트 관점)")
    parser.add_argument(
        "--llm-debug", default=DEFAULT_LLM_DEBUG, help="llm-mock base URL (호스트 관점)"
    )
    parser.add_argument(
        "--loki-url",
        default=DEFAULT_LOKI_URL,
        help="백엔드가 보는 Loki 주소. 백엔드를 호스트에서 돌리면 http://localhost:3100",
    )
    parser.add_argument(
        "--llm-base-url",
        default=DEFAULT_LLM_BASE_URL,
        help="백엔드가 보는 llm-mock OpenAI 호환 엔드포인트",
    )
    parser.add_argument("--llm-model", default=DEFAULT_LLM_MODEL)
    parser.add_argument(
        "--username", default=DEFAULT_USERNAME, help="백엔드 로그인 계정 (기본 admin)"
    )
    parser.add_argument(
        "--password", default=DEFAULT_PASSWORD, help="백엔드 로그인 비밀번호 (기본 admin)"
    )
    args = parser.parse_args()

    print("=" * 72)
    print("AILA E2E 데모 — Compose 스택으로 전 경로 재현")
    print("=" * 72)
    print(f"backend  : {args.api}")
    print(f"demo-api : {args.demo}")
    print(f"llm-mock : {args.llm_debug}  (백엔드 관점 {args.llm_base_url})")
    print(f"loki     : {args.loki_url}  (백엔드 관점)")

    secrets = load_demo_secrets()
    print(f"감사 대상 가짜 비밀값 {len(secrets)} 종: {', '.join(sorted(secrets))}")

    api = Api(args.api)
    started = time.monotonic()
    try:
        step_preflight(api, args)
        loki_id = step_loki_connection(api, args)
        step_llm_connection(api, args)
        step_trigger_scenarios(args)

        step("d. 정책 생성 → 미리보기 → 실행(query-run) → 오류 그룹")
        error_policy = step_policy(
            api,
            connection_id=loki_id,
            name=ERROR_POLICY_NAME,
            logql=ERROR_LOGQL,
            description="payment-api 의 ERROR 로그. 시나리오 01·05 의 급증을 본다.",
        )
        error_run = step_preview_and_run(api, policy=error_policy, connection_id=loki_id)

        secret_policy = step_policy(
            api,
            connection_id=loki_id,
            name=SECRET_POLICY_NAME,
            logql=SECRET_LOGQL,
            description="시나리오 06. 마스킹이 화면·전송 두 경로 모두에서 걸리는지 본다.",
        )
        secret_run = step_preview_and_run(api, policy=secret_policy, connection_id=loki_id)

        secret_group = step_masking_on_screen(api, run=secret_run, secrets=secrets)

        step("f. AI 분석 실행 → 폴링 → succeeded (hypotheses·limitations 확인)")
        jobs = [step_analyze(api, group_id=secret_group["id"], label="시나리오 06")]

        error_groups = api.get(f"/api/query-runs/{error_run['id']}/error-groups")
        top_group = max(error_groups["items"], key=lambda item: item["count"])
        info(
            f"ERROR 정책의 최다 그룹 id={top_group['id']} count={top_group['count']} "
            f"error_type={top_group['error_type']}"
        )
        jobs.append(step_analyze(api, group_id=top_group["id"], label="최다 오류 그룹"))

        step_payload_audit(args, secrets)
        step_report_usage_dashboard(api, jobs=jobs, policy=error_policy, secrets=secrets)

    except Fail as exc:
        print(f"\nFAIL  {exc}", file=sys.stderr)
        print("\n=== ALL PASS 아님 — 위 단계에서 중단 ===", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"\nFAIL  HTTP 오류: {exc!r}", file=sys.stderr)
        return 1
    finally:
        api.close()

    elapsed = time.monotonic() - started
    print("\n" + "=" * 72)
    print(f"ALL PASS — {_step_no} 단계, {elapsed:.1f}s")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
