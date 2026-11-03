# MTS Rates: one image for the web app and the two Cloud Run jobs (migrate, import_sheets).

# 1. Screens
FROM node:22-slim AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# 2. API and jobs
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY app ./app
COPY db ./db
COPY --from=web /src/frontend/dist ./frontend/dist
RUN useradd --system --uid 10001 mts
USER mts
# Cloud Run sets PORT. The jobs override the command (python -m app.db.migrate / app.db.import_sheets).
CMD exec uvicorn app.web.main:create_app --factory --host 0.0.0.0 --port "${PORT:-8080}" \
    --proxy-headers --forwarded-allow-ips="*" --no-server-header
