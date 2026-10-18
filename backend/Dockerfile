# syntax=docker/dockerfile:1

# --- Build stage ------------------------------------------------------------
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Build-only toolchain, left behind in the runtime image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# --- Runtime stage ----------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/* \
 # Runs unprivileged: a container that writes code should not also be root.
 && useradd --create-home --uid 10001 evergent \
 # The file store's mount point, owned by that user. A new Docker volume
 # mounted here takes this ownership; without it the volume is root's and
 # every upload fails.
 && mkdir -p /data/storage && chown -R evergent:evergent /data

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=evergent:evergent . .

USER evergent
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
