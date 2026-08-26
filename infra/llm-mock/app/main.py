"""OpenAI 호환 chat completions 스텁 + 전송 페이로드 감사용 디버그 엔드포인트.

왜 있는가
---------
E2E 데모의 성공 기준은 "Docker Compose 만으로 데모 환경과 장애 시나리오를 재현할 수
있다"이다. 분석 단계에 실제 LLM 키가 필요하면 그 기준이 깨지고, 무엇보다 설계 문서가
요구하는 **"LLM 요청 페이로드에 원문 토큰이 없음을 단언하는 자동 테스트"**를 돌릴 수
없다 — 진짜 프로바이더에 보낸 요청 본문은 되돌려 받을 수 없기 때문이다.

그래서 이 서비스는 두 가지만 한다.

1. `POST /v1/chat/completions` — 요청의 `response_format.json_schema` 를 읽어
   **스키마에 맞는 분석 결과 JSON**(한국어)을 돌려주고 `usage` 토큰을 채운다.
2. `GET /debug/last-request` — 방금 받은 요청 본문을 그대로 노출한다.
   마스킹 감사(원문 비밀값이 페이로드에 없음)를 여기에서 단언한다.

**외부로 나가는 네트워크 호출은 없다.** 프롬프트는 프로세스 메모리에만 있고 재시작하면
사라진다. 로컬·데모 전용이므로 인증도 없다 — 이 컨테이너를 외부에 노출하지 말 것.
"""

from __future__ import annotations

import math
import os
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .generator import build_analysis_json

MODEL_FALLBACK = os.getenv("LLM_MOCK_MODEL", "llm-mock-1")
#: 메모리에 보관하는 최근 요청 수. 감사에 필요한 건 마지막 한 건이지만
#: 여러 그룹을 연속 분석했을 때 어느 요청이 샜는지 보려면 이력이 있어야 한다.
HISTORY_SIZE = int(os.getenv("LLM_MOCK_HISTORY", "50"))
#: 응답 지연(ms). 프런트의 폴링·로딩 상태를 눈으로 볼 때 올린다.
LATENCY_MS = int(os.getenv("LLM_MOCK_LATENCY_MS", "0"))

#: 연결 테스트(`max_completion_tokens=1`, 프롬프트 "ping")에 돌려줄 본문.
PING_REPLY = "pong"

app = FastAPI(
    title="AILA llm-mock",
    version="1.0.0",
    description=(
        "OpenAI 호환 /v1/chat/completions 스텁. 요청의 json_schema 에 맞는 분석 결과를 "
        "돌려주고, 받은 요청 본문을 /debug/last-request 로 노출한다. 로컬·데모 전용."
    ),
)

_lock = Lock()
_history: deque[dict[str, Any]] = deque(maxlen=HISTORY_SIZE)
_counter = {"total": 0, "analysis": 0, "ping": 0}


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    """대략적인 토큰 수. 한국어가 섞여 있으니 3 자 = 1 토큰으로 잡는다.

    실제 토크나이저가 아니다 — usage 집계 화면이 0 이 아닌 값으로 채워지는지
    확인하는 것이 목적이다.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 3))


def _messages_text(messages: list[Any]) -> str:
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            # 멀티모달 형식(`[{"type": "text", "text": ...}]`) 도 받아 둔다.
            for chunk in content:
                if isinstance(chunk, dict) and isinstance(chunk.get("text"), str):
                    parts.append(chunk["text"])
    return "\n".join(parts)


def _schema_from(body: dict[str, Any]) -> dict[str, Any] | None:
    """`response_format` 의 json_schema 를 꺼낸다. 없으면 None (= 자유 형식)."""
    response_format = body.get("response_format")
    if not isinstance(response_format, dict):
        return None
    if response_format.get("type") not in (None, "json_schema", "json_object"):
        return None
    wrapper = response_format.get("json_schema")
    if not isinstance(wrapper, dict):
        return None
    schema = wrapper.get("schema")
    return schema if isinstance(schema, dict) else None


def _record(body: dict[str, Any], headers: dict[str, str], kind: str) -> dict[str, Any]:
    """요청을 감사용으로 보관한다. Authorization 헤더는 값 대신 존재 여부만 남긴다."""
    safe_headers = {
        key: value
        for key, value in headers.items()
        if key.lower() not in ("authorization", "api-key", "x-api-key", "cookie")
    }
    entry = {
        "received_at": datetime.now(UTC).isoformat(),
        "kind": kind,
        "model": body.get("model"),
        "messages": body.get("messages", []),
        # 프롬프트 원문을 한 덩어리로 — 감사 스크립트가 여기만 훑으면 되게 한다.
        "prompt_text": _messages_text(body.get("messages") or []),
        "response_format": body.get("response_format"),
        "body": body,
        "headers": safe_headers,
        "has_authorization_header": any(key.lower() == "authorization" for key in headers),
    }
    with _lock:
        _history.append(entry)
        _counter["total"] += 1
        _counter[kind] = _counter.get(kind, 0) + 1
    return entry


def _error(status_code: int, message: str, code: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": code, "param": None, "code": code}},
    )


# ---------------------------------------------------------------------------
# OpenAI 호환 엔드포인트
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
def health() -> dict[str, Any]:
    with _lock:
        counts = dict(_counter)
    return {"status": "ok", "service": "llm-mock", "requests": counts}


@app.get("/v1/models", tags=["openai"])
def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_FALLBACK,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "aila-llm-mock",
            }
        ],
    }


@app.post("/v1/chat/completions", tags=["openai"])
async def chat_completions(request: Request) -> Any:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 본문이 JSON 이 아닌 경우
        return _error(400, "요청 본문이 JSON 이 아닙니다.")
    if not isinstance(body, dict):
        return _error(400, "요청 본문이 object 가 아닙니다.")
    if body.get("stream"):
        return _error(400, "llm-mock 은 stream=true 를 지원하지 않습니다.")

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return _error(400, "messages 가 필요합니다.")

    schema = _schema_from(body)
    kind = "analysis" if schema is not None else "ping"
    entry = _record(body, dict(request.headers), kind)
    prompt_text = entry["prompt_text"]

    if LATENCY_MS > 0:
        import asyncio

        await asyncio.sleep(LATENCY_MS / 1000)

    if schema is None:
        # 연결 테스트 경로. 최소 토큰으로 오므로 본문도 최소로 돌려준다.
        content = PING_REPLY
    else:
        try:
            import json

            content = json.dumps(
                build_analysis_json(schema, prompt_text), ensure_ascii=False, indent=None
            )
        except Exception as exc:  # noqa: BLE001 - 스키마가 예상 밖인 경우
            return _error(500, f"스키마로 응답을 만들지 못했습니다: {exc}", code="server_error")

    prompt_tokens = _estimate_tokens(prompt_text)
    completion_tokens = _estimate_tokens(content)
    model = str(body.get("model") or MODEL_FALLBACK)

    return {
        "id": f"chatcmpl-mock-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "system_fingerprint": "fp_aila_llm_mock",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "refusal": None},
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0, "audio_tokens": 0},
        },
    }


# ---------------------------------------------------------------------------
# 감사용 디버그 엔드포인트
# ---------------------------------------------------------------------------


@app.get("/debug/last-request", tags=["debug"])
def last_request(kind: str | None = None) -> Any:
    """마지막으로 받은 요청. `kind=analysis` 로 연결 테스트(ping)를 걸러낼 수 있다.

    마스킹 감사는 `prompt_text` 하나만 보면 된다 — system·user 메시지를 이어붙인
    문자열이다. 원문 비밀값이 하나라도 있으면 마스킹 경로가 샌 것이다.
    """
    with _lock:
        entries = list(_history)
    if kind:
        entries = [item for item in entries if item["kind"] == kind]
    if not entries:
        return JSONResponse(
            status_code=404,
            content={"detail": "아직 받은 요청이 없습니다.", "kind": kind},
        )
    return entries[-1]


@app.get("/debug/requests", tags=["debug"])
def list_requests(kind: str | None = None, limit: int = 20) -> dict[str, Any]:
    """최근 요청 목록 (오래된 것 → 최신 순)."""
    with _lock:
        entries = list(_history)
        counts = dict(_counter)
    if kind:
        entries = [item for item in entries if item["kind"] == kind]
    limit = max(1, min(limit, HISTORY_SIZE))
    return {"total_received": counts, "returned": len(entries[-limit:]), "items": entries[-limit:]}


@app.post("/debug/reset", tags=["debug"])
def reset() -> dict[str, Any]:
    """보관 중인 요청을 지운다. E2E 스크립트를 다시 돌릴 때 쓴다."""
    with _lock:
        cleared = len(_history)
        _history.clear()
        for key in list(_counter):
            _counter[key] = 0
    return {"cleared": cleared}
