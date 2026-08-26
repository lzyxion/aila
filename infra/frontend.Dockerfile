# AILA 프론트엔드 개발 서버 이미지 (compose profile "app" 전용)
#
# 빌드 컨텍스트는 `./frontend`, Dockerfile 만 `infra/` 에 둔다 (파일 소유권 규칙).
#
# [전제] frontend/package.json 이 있어야 빌드된다. 없으면 실패하지만
# profile "app" 뒤에 있으므로 기본 `docker compose up` 은 영향을 받지 않는다.
#
# 프로덕션 빌드(nginx 정적 서빙)가 아니라 **개발 서버**다. MVP 배포 대상이
# 로컬·데모로 한정되어 있어 정적 빌드 이미지를 따로 둘 이유가 없다.

FROM node:22-alpine

ENV NODE_ENV=development \
    CI=true

WORKDIR /app

# package-lock.json 이 있으면 npm ci, 없으면 npm install.
COPY package.json ./
COPY package-lock.jso[n] ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY . .

EXPOSE 5173

# --host 0.0.0.0 이 없으면 컨테이너 밖에서 접속되지 않는다.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
