# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency spec only — layer cache for pip
COPY pyproject.toml .

# Install dependencies into an isolated prefix
RUN pip install --upgrade pip \
 && pip install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    pydantic \
    pydantic-settings \
    sqlglot \
    python-multipart \
    aiofiles \
    structlog \
    rich \
    python-dotenv \
    httpx


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL maintainer="BI Platform Team"
LABEL description="Redshift → Microsoft Fabric DDL Converter API"

WORKDIR /app

# Non-root user for security
RUN addgroup --system converter && adduser --system --ingroup converter converter

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY app/       ./app/
COPY config/    ./config/
COPY .env.example .env

# Data directories
RUN mkdir -p data/uploads data/outputs data/reports data/logs \
 && chown -R converter:converter /app

USER converter

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.api.app:create_app", \
     "--factory", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--log-level", "info"]
