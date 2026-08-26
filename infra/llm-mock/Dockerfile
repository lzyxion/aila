# AILA llm-mock — OpenAI 호환 /v1/chat/completions 스텁
#
# 외부로 나가는 네트워크 호출이 전혀 없다. E2E 데모에서 실제 LLM 키 없이
# 분석 플로우를 끝까지 돌리고, 전송된 프롬프트 원문을 감사하기 위한 서비스다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 비특권 사용자로 실행 (데모라도 root 로 돌릴 이유가 없다)
RUN useradd --create-home --uid 10002 mock
USER mock

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--no-server-header", "--log-level", "info"]
