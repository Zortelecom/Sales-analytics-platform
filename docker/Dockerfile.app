# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────────────────────
# Sales Analytics Platform — Shared Application Image
#
# One image serves all Python services by overriding CMD:
#   dagster-daemon:    dagster-daemon run
#   dagster-webserver: dagster-webserver --host 0.0.0.0 --port 3000
#   streamlit:         streamlit run reporting/app.py ...
#
# Build args (injected by CI for traceability):
#   BUILD_DATE, GIT_COMMIT
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS builder

ARG BUILD_DATE=unknown
ARG GIT_COMMIT=unknown

# Build-time dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Dependency install (layer-cached separately from source code) ─────────────
COPY pyproject.toml README.md ./

# Create stub packages so editable install resolves correctly
RUN mkdir -p ingestion serving orchestration shared reporting sqlmesh && \
    touch ingestion/__init__.py serving/__init__.py orchestration/__init__.py \
          shared/__init__.py reporting/__init__.py

# Production install only (no [dev] extras → no pytest/ruff/mypy in prod)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e "." && \
    # Install dagster-postgres for PostgreSQL run storage backend
    pip install --no-cache-dir "dagster-postgres==1.12.12"

# ── Source code ───────────────────────────────────────────────────────────────
COPY ingestion/   ./ingestion/
COPY sqlmesh/     ./sqlmesh/
COPY serving/     ./serving/
COPY orchestration/ ./orchestration/
COPY shared/      ./shared/
COPY reporting/   ./reporting/

# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ARG BUILD_DATE=unknown
ARG GIT_COMMIT=unknown

# Runtime labels (visible in `docker inspect`)
LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.title="sap-app" \
      org.opencontainers.image.description="Sales Analytics Platform — Python services"

# Minimal runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Python environment and application from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

# ── Dagster home ──────────────────────────────────────────────────────────────
ENV DAGSTER_HOME=/dagster-home
RUN mkdir -p $DAGSTER_HOME

# Dagster production configuration
COPY docker/dagster/dagster.yaml   $DAGSTER_HOME/dagster.yaml
COPY docker/dagster/workspace.yaml $DAGSTER_HOME/workspace.yaml

# ── Data directories (bind-mounted in production, pre-created for local dev) ──
RUN mkdir -p \
    data/source \
    data/input \
    data/archive \
    data/dead_letter \
    data/warehouse \
    data/exports/csv \
    data/exports/parquet \
    data/exports/quality_reports

# ── SQLMesh state directory (named volume mounted at /sqlmesh-state) ──────────
RUN mkdir -p /sqlmesh-state

# ── Non-root user for security ────────────────────────────────────────────────
RUN useradd -r -u 1001 -g root sap && \
    chown -R sap:root /app /dagster-home /sqlmesh-state

USER sap

# Expose informational ports (actual binding done via docker-compose)
EXPOSE 3000 8501

# Default command — overridden per service in docker-compose.prod.yml
CMD ["dagster-webserver", "--host", "0.0.0.0", "--port", "3000"]
