# syntax=docker/dockerfile:1.7
# One image: builds the SPA, then runs FastAPI, which serves /api and the built frontend.

# ---- frontend build -------------------------------------------------------------------------
FROM node:24-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- runtime --------------------------------------------------------------------------------
FROM python:3.13-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# LibreOffice (writer only, headless) converts DOCX agreements to PDF. Carlito/Caladea are
# metric-compatible with Calibri/Cambria and Liberation with Arial/Times, so Word documents
# keep their line breaks and pagination after conversion.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui \
      fonts-liberation fonts-dejavu-core fonts-crosextra-carlito fonts-crosextra-caladea \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv

# Non-root user owns the app from the start (COPY --chown) so no `chown -R` layer
# duplicates the virtualenv.
RUN useradd --create-home --uid 10001 app && mkdir -p /app/backend && chown app:app /app /app/backend
USER app
WORKDIR /app/backend
COPY --chown=app:app backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache
COPY --chown=app:app backend/ ./
COPY --chown=app:app --from=frontend /build/frontend/dist /app/frontend/dist

ENV PATH="/app/backend/.venv/bin:${PATH}" \
    HOME=/home/app \
    APP_ENV=prod \
    FRONTEND_DIST=/app/frontend/dist \
    LIBREOFFICE_BIN=soffice

EXPOSE 8000
# Render injects $PORT. Proxy headers so request.client reflects the real client IP
# (recorded in the signing audit trail).
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 20"]
