# AILA 백엔드 이미지 (compose profile "app" 전용)
#
# 빌드 컨텍스트는 `./backend` 이고 이 Dockerfile 만 `infra/` 에 있다.
# 인프라 트랙이 backend/ 안에 파일을 만들지 않기 위한 배치다 (파일 소유권 규칙).
#
#   docker compose --profile app build backend
#
# [알려진 문제] backend/ 에 `.dockerignore` 가 없어 `.venv/` 까지 빌드 컨텍스트로
# 전송된다. COPY 대상에는 들어가지 않으므로 이미지는 깨끗하지만 빌드가 느리다.
# backend 소유 트랙이 `backend/.dockerignore` 에 `.venv/`, `__pycache__/`,
# `.pytest_cache/`, `*.egg-info/` 를 추가하면 해결된다.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# psycopg[binary] 를 쓰므로 libpq 를 따로 깔지 않아도 된다.
# curl 은 healthcheck·수동 확인용.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 의존성 레이어를 소스와 분리한다. pyproject.toml 은 freeze 파일이라 자주 바뀌지 않는다.
# setuptools 의 packages.find 가 app 패키지를 찾아야 하므로 app/ 도 함께 넣는다.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir -e .

COPY alembic.ini ./
COPY alembic ./alembic

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# 마이그레이션은 자동 실행하지 않는다. 스키마 변경 시점을 사람이 통제해야 한다.
#   docker compose --profile app exec backend alembic upgrade head
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
