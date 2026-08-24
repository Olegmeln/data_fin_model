# Data Fin Model — production image for the FastAPI backend.
# Build context: repository root (needed so backend/ and .env.example are both visible).
#
#   docker build -t data-fin-model .
#   docker run --rm -p 8000:8000 -e DATABASE_URL=sqlite:////data/finmodel.db -v dfm_data:/data data-fin-model
#
# For local development with Postgres, use docker-compose.yml instead.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps: libpq for psycopg2 (Postgres), curl for the healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching on repeated builds).
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt psycopg2-binary

# Application code.
COPY backend /app/backend
COPY .env.example /app/.env.example

WORKDIR /app/backend

# Runs as non-root.
RUN useradd --create-home --shell /bin/bash dfm && chown -R dfm:dfm /app
USER dfm

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
