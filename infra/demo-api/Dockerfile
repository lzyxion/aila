# AILA 데모 API — 장애 시나리오 로그 생산자
FROM python:3.12-slim

# PYTHONUNBUFFERED 가 이 이미지의 핵심 설정이다.
# 버퍼링이 켜져 있으면 로그가 stdout 에 뒤늦게 몰려 나오고, Alloy 가 붙이는
# 수집 시각이 실제 발생 시각과 크게 벌어져 '빈도 급증' 시나리오가 무너진다.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 비특권 사용자로 실행 (데모라도 root 로 돌릴 이유가 없다)
RUN useradd --create-home --uid 10001 demo
USER demo

EXPOSE 8000

# uvicorn 의 access log 는 비정형(non-JSON)이라 일부러 켜 둔다 —
# `| json` 파싱 실패 케이스의 자연스러운 공급원이다.
CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--no-server-header", "--log-level", "info"]
