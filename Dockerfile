# Stage 1: build UI
FROM node:20-alpine AS ui-builder
WORKDIR /ui
COPY ui/package*.json ./
RUN npm install --silent --prefer-offline
COPY ui/ ./
RUN npm run build

# Stage 2: Python backend
FROM python:3.12-slim

# Build metadata - stamp at build time, e.g.
#   docker build --build-arg VERSION=0.1.0 \
#                --build-arg VCS_REF=$(git rev-parse --short HEAD) \
#                --build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) .
ARG VERSION=dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="reach" \
      org.opencontainers.image.description="Controlled command bridge for AI agents to operate remote machines" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

# Predictable Python runtime: unbuffered logs, no .pyc clutter, no pip cache.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY backend/adapters/fastapi/requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY backend/ .

# Copy built UI
COPY --from=ui-builder /ui/dist /app/ui_dist

ENV STORAGE_BACKEND=postgres
ENV RELEASES_S3_BASE=https://reach-releases.s3.amazonaws.com

# Run as non-root by default, while staying compatible with ANY uid - including
# `--user 0` (root) and the random uids Kubernetes/OpenShift assign. Those
# platforms run arbitrary uids in group 0, so we grant the root group access to
# /app rather than chowning to one fixed user. The backend needs no root
# privileges and writes nothing to disk (migrations go to the external DB, logs
# to stdout), so group read/exec is sufficient; root override still works
# because root bypasses file permissions.
RUN chgrp -R 0 /app && chmod -R g=rwX /app
USER 10001

EXPOSE 8000

# Liveness via the app's own /health endpoint (no curl in the slim image, use python).
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).status==200 else 1)" || exit 1

# Bootstrap storage before serving: Alembic migrations for Postgres, or table
# creation for DynamoDB (STORAGE_BACKEND=dynamo). Both are idempotent.
CMD ["sh", "-c", "if [ \"$STORAGE_BACKEND\" = \"dynamo\" ]; then python -m shared.dynamo_bootstrap; else alembic upgrade head; fi && uvicorn adapters.fastapi.main:app --host 0.0.0.0 --port 8000"]
