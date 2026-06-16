FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for layer caching
COPY backend/adapters/fastapi/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ .

ENV STORAGE_BACKEND=postgres
ENV RELEASES_S3_BASE=https://reach-releases.s3.amazonaws.com

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn adapters.fastapi.main:app --host 0.0.0.0 --port 8000"]
