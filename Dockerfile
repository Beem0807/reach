# Stage 1: build UI
FROM node:20-alpine AS ui-builder
WORKDIR /ui
COPY ui/package*.json ./
RUN npm install --silent --prefer-offline
COPY ui/ ./
RUN npm run build

# Stage 2: Python backend
FROM python:3.12-slim
WORKDIR /app

COPY backend/adapters/fastapi/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# Copy built UI
COPY --from=ui-builder /ui/dist /app/ui_dist

ENV STORAGE_BACKEND=postgres
ENV RELEASES_S3_BASE=https://reach-releases.s3.amazonaws.com

EXPOSE 8000

# Bootstrap storage before serving: Alembic migrations for Postgres, or table
# creation for DynamoDB (STORAGE_BACKEND=dynamo). Both are idempotent.
CMD ["sh", "-c", "if [ \"$STORAGE_BACKEND\" = \"dynamo\" ]; then python -m shared.dynamo_bootstrap; else alembic upgrade head; fi && uvicorn adapters.fastapi.main:app --host 0.0.0.0 --port 8000"]
