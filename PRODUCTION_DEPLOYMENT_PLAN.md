# Production Deployment Plan — Sales Analytics Platform
> **Author**: Data Engineering  
> **Date**: 2026-05-30  
> **Priority**: ASAP — CEO directive  
> **Constraint**: No Kubernetes; Docker Compose; local-first with hybrid evolution path

---

## 0. Executive Summary

The platform is architecturally mature. Dead-letter handling, environment namespacing, data quality gates,
prod_promotion_sensor, and SCD-safe joins are all already in place. The gaps are purely at the
**infrastructure layer**: process supervision, secret hygiene, Dagster run persistence, and a security
boundary around the exposed services.

**What this plan delivers:**
- Week 1: harden the codebase for production (secrets, logging, SQLMesh/Dagster backends)
- Week 2: full Docker Compose stack running locally — Dagster + Streamlit + Apache Superset behind Nginx
- Month 2+: hybrid cloud path via object storage swap (MinIO → S3/Azure Blob) and optional compute offload

**The platform will NOT require Kubernetes at any point in this plan.**

---

## 1. Pre-Deployment Assessment

### ✅ Already Production-Ready

| Component | Notes |
|---|---|
| Dead-letter handling | Corrupt files quarantined to `data/dead_letter/`; Dagster asset check alerts on non-empty |
| Environment namespacing | `dev`/`prod` isolated in DuckLake parquet paths and serving DB filename |
| `prod_promotion_sensor` | Requires successful dev run < 24 h before prod job can trigger |
| Data quality audits | 10+ AUDIT blocks, each returning failing rows (not a count) |
| File-swap serving | Atomic rename — safe for production; Quack stays disabled until DuckDB v2.0 (Sept 2026) |
| Typed config via Pydantic BaseSettings | Ready for Docker env-var injection with no code change |
| pytest + SQLMesh unit tests | Full CI-ready test suite |
| Archive management | Batched archiving keyed by `batch_id` |
| SCD joins (NULL-safe) | Open-ended range guard (`valid_to IS NULL OR …`), not BETWEEN |
| Export event bus | Decoupled async exports, independently toggled |

### ❌ Gaps to Close Before Go-Live

| Gap | Severity | Resolution |
|---|---|---|
| Dagster uses SQLite backend by default | **Critical** — run history lost on container recreate | Migrate to PostgreSQL (§3.2) |
| Secrets in plain `.env` file | **Critical** — credential exposure risk | `.env.prod` never committed; permissions 600 (§3.1) |
| `sqlmesh_state.db` at project root | **High** — wiped on container rebuild | Mount on named Docker volume; update SQLMesh config (§3.3) |
| No process supervisor | **High** — crash = permanent outage | `restart: unless-stopped` on all services |
| No reverse proxy / TLS | **High** — plaintext HTTP, no auth on Dagster UI | Nginx + mkcert for local; Let's Encrypt for remote (§4.4) |
| No structured logging | **Medium** — ops blind in production | JSON logging driver + `logging` module throughout (§3.4) |
| Superset not containerized | **Medium** — mentioned in arch, absent from codebase | Added to Compose stack (§4.2) |
| No backup strategy | **High** — data loss on disk failure | Nightly tar.zst + cron (§4.5) |
| SharePoint sync not automated | **Medium** — data/source/ filled manually | Documented + optional Graph API job (§4.7) |

---

## 2. Target Architecture

### Local Production Stack (Phase 1 — Week 2)

```
┌───────────────────── Docker Host (single machine) ──────────────────────────────┐
│                                                                                   │
│  ┌──────────────── sap-network (internal bridge) ──────────────────────────────┐ │
│  │                                                                               │ │
│  │  dagster-daemon ──────────────────────────────────────────────────────────┐  │ │
│  │  (schedules, sensors, run queue, no port)                                  │  │ │
│  │                                                                             ↓  │ │
│  │  dagster-webserver (:3000) ←──── postgres:5432 (Dagster run storage)       │  │ │
│  │                                                                             │  │ │
│  │  streamlit (:8501) ─────────────────────────────────────┐                  │  │ │
│  │  superset (:8088) + superset-worker ────────────────────┤                  │  │ │
│  │    ├── postgres:5432 (Superset metadata)                │                  │  │ │
│  │    └── redis:6379  (Celery broker)                      │                  │  │ │
│  │                                                          ↓                  │  │ │
│  │  nginx (:80, :443, :3443, :8443) ────────────────── [TLS + BasicAuth]      │  │ │
│  │    ├── /       → streamlit:8501       (Reports)                            │  │ │
│  │    ├── :3443   → dagster-webserver    (Engineering, Basic Auth)            │  │ │
│  │    └── :8443   → superset:8088        (Enterprise BI, Superset Auth)       │  │ │
│  │                                                                              │ │
│  └──────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                   │
│  Bind mount (host):  /opt/sap/data/  →  /app/data/  (all containers)            │
│  Named volumes:      sap_postgres_data, sap_redis_data, sap_dagster_home,        │
│                      sap_sqlmesh_state                                           │
│                                                                                   │
│  Exposed host ports: 80 (redirect), 443 (reports), 3443 (dagster), 8443 (bi)    │
└───────────────────────────────────────────────────────────────────────────────────┘
```

### Hybrid Evolution Target (Phase 2 — Month 2+)

```
Phase 2a — Object Storage (zero code change, only config)
──────────────────────────────────────────────────────────
  Local:   DuckLake → /opt/sap/data/warehouse/parquet_storage/
  Step 1:  Add MinIO to Compose → s3://sap-local/
  Step 2:  Swap endpoint to real S3/Azure Blob
  DuckDB httpfs extension handles both transparently

Phase 2b — Compute Offload (3 options, pick one)
──────────────────────────────────────────────────────────
  Option A: Dagster Cloud Hybrid           (easiest migration)
    Control plane in Dagster Cloud, agent runs locally
    dagster.yaml storage config change only; all code unchanged
    ~$200/month for a small team

  Option B: Cloud VM                       (budget-friendly)
    Deploy same Docker Compose stack to t3.medium/B2s (~$30-35/month)
    Serving DB stays local for low-latency BI; incremental Parquet exports sync data
    Or: MotherDuck for cloud DuckDB serving (DuckDB JDBC, Streamlit-native)

  Option C: Cloudflare Tunnel              (zero cloud infra cost)
    Zero-trust tunnel from local host → public URL
    No public IP, no VPN, no open firewall ports
    Keep all compute and storage local; expose only Superset and reports

Phase 2c — BI Migration
──────────────────────────────────────────────────────────
  Preset.io: managed Superset → point at MotherDuck or cloud DuckDB
  Streamlit Community Cloud: free hosting for the operational dashboard
  Or: self-hosted on cloud VM from Phase 2b (same Docker image, no change)
```

---

## 3. Phase 0: Hardening (Week 1 — Before Containerization)

### 3.1 Secret Management

Never store a secret in a Dockerfile, docker-compose.yml, or any git-tracked file.
All secrets live in `.env.prod`, owned by a dedicated service account:

```bash
# Create service account
sudo useradd -r -s /bin/false sap-service
sudo mkdir -p /opt/sap
sudo chown sap-service:sap-service /opt/sap

# Secure the secrets file
sudo cp .env.prod.example /opt/sap/.env.prod
sudo chown sap-service:sap-service /opt/sap/.env.prod
sudo chmod 600 /opt/sap/.env.prod
```

Generate secrets securely:
```bash
# Postgres password
python3 -c "import secrets; print(secrets.token_hex(32))"

# Superset secret key — must be STABLE across restarts; store and never rotate casually
python3 -c "import secrets; print(secrets.token_hex(64))"

# Redis password
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Nginx BasicAuth for Dagster UI
sudo apt install -y apache2-utils
htpasswd -c docker/nginx/.htpasswd dagster-admin
```

### 3.2 Dagster Production Backend (PostgreSQL)

The default SQLite backend is fine for development. In production, a container recreate loses all run history,
schedules, and event logs. PostgreSQL persists these in a named volume.

Create `docker/dagster/dagster.yaml`:
```yaml
storage:
  postgres:
    postgres_url:
      env: DAGSTER_POSTGRES_URL

scheduler:
  module: dagster.core.scheduler
  class: DagsterDaemonScheduler

run_coordinator:
  module: dagster.core.run_coordinator
  class: QueuedRunCoordinator
  config:
    max_concurrent_runs: 2

run_monitoring:
  enabled: true
  start_timeout_seconds: 300
  max_resume_run_attempts: 3

telemetry:
  enabled: false
```

Install the Dagster Postgres extra (add to `pyproject.toml`):
```toml
[project.dependencies]
dagster-postgres = "==1.12.12"   # Pin to same version as dagster
```

### 3.3 SQLMesh State on a Named Volume

`sqlmesh_state.db` is currently at the project root — the container filesystem. A container rebuild wipes it.

**Fix in `sqlmesh/config.yaml`** — change state connection to use a dedicated path:
```yaml
# sqlmesh/config.yaml
gateways:
  dev:
    state_connection:
      type: duckdb
      database: /sqlmesh-state/sqlmesh_state.db   # ← mounted as named volume sap_sqlmesh_state
  prod:
    state_connection:
      type: duckdb
      database: /sqlmesh-state/sqlmesh_state.db
```

In Docker Compose, mount `sap_sqlmesh_state:/sqlmesh-state` on every app container.
The path `/sqlmesh-state/` resolves correctly for both local dev (via a relative bind) and Docker (via named volume).

**Local dev compatibility**: set `SQLMESH_STATE_PATH=/path/to/local/sqlmesh-state/` and symlink, or keep
the old root path for local dev and only use the volume path inside Docker.

### 3.4 Structured Logging

Dagster assets already use `context.log` — no change needed there. The ingestion CLI and serving CLI
likely use `print()`. Replace with standard `logging`:

```python
# Add to each entry point (ingestion/main.py, serving/cli.py, etc.)
import logging, sys

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","module":"%(module)s","msg":"%(message)s"}',
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Replace: print(f"Processing {file}")
# With:    logger.info("Processing file", extra={"file": str(file)})
```

Docker's JSON log driver then handles rotation (configured in Compose with `max-size: "50m"`, `max-file: "10"`).

---

## 4. Phase 1: Local Production Deployment (Week 2)

### 4.1 File Layout (additions to repo)

```
sales-analytics-platform/
├── docker/
│   ├── Dockerfile.app                 ← Single image for all Python services
│   ├── docker-compose.prod.yml        ← Full production stack
│   ├── dagster/
│   │   ├── dagster.yaml               ← PostgreSQL backend, QueuedRunCoordinator
│   │   └── workspace.yaml             ← Points to orchestration package
│   ├── nginx/
│   │   ├── nginx.conf                 ← TLS termination, reverse proxy, BasicAuth
│   │   ├── .htpasswd                  ← Dagster UI credentials (gitignored)
│   │   └── certs/                     ← TLS certs (gitignored)
│   ├── postgres/
│   │   └── init.sql                   ← Creates dagster + superset databases
│   ├── superset/
│   │   └── superset_config.py         ← Redis cache, proxy fix, security settings
│   └── scripts/
│       ├── backup.sh                  ← Nightly backup + rotation
│       └── healthcheck.sh             ← Full stack health probe
├── .env.prod.example                  ← All variables documented, no values
└── .gitignore (additions):
    docker/nginx/certs/
    docker/nginx/.htpasswd
    .env.prod
```

### 4.2 Service Summary

| Service | Image | Internal port | Role |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | 5432 | Dagster run storage + Superset metadata |
| `redis` | `redis:7-alpine` | 6379 | Superset Celery broker/cache |
| `dagster-daemon` | `./Dockerfile.app` | — | Schedules, sensors, run execution |
| `dagster-webserver` | `./Dockerfile.app` | 3000 | Dagster UI (engineers) |
| `streamlit` | `./Dockerfile.app` | 8501 | Operational dashboards (end users) |
| `superset` | `apache/superset:4.1.1` | 8088 | Enterprise BI — reports, RBAC |
| `superset-worker` | `apache/superset:4.1.1` | — | Async query execution (Celery) |
| `nginx` | `nginx:1.27-alpine` | **80, 443, 3443, 8443** | TLS termination, routing, BasicAuth |

### 4.3 Volume Strategy

| Volume | Type | Mounted at | Backup priority | Notes |
|---|---|---|---|---|
| `sap_postgres_data` | Named | `/var/lib/postgresql/data` | **High** | Dagster history + Superset metadata |
| `sap_redis_data` | Named | `/data` | Low | Cache; loss is safe, Celery retries |
| `sap_dagster_home` | Named | `/dagster-home` | Medium | Dagster instance config + log artifacts |
| `sap_sqlmesh_state` | Named | `/sqlmesh-state` | **High** | SQLMesh run state DB |
| `$DATA_ROOT` | Bind mount | `/app/data` | **Critical** | All DuckLake parquet, serving DBs, archives |

`$DATA_ROOT` (default `/opt/sap/data`) is the single directory to back up for data recovery.

### 4.4 TLS Certificates

**Local (mkcert — no browser warnings):**
```bash
# Install mkcert
brew install mkcert   # macOS
# apt install mkcert  # Ubuntu

mkcert -install   # Adds root CA to system trust store

# Generate certs
mkcert \
  -cert-file docker/nginx/certs/local.crt \
  -key-file  docker/nginx/certs/local.key \
  sales-analytics.local localhost 127.0.0.1

# /etc/hosts entry (on all client machines)
echo "127.0.0.1  sales-analytics.local" | sudo tee -a /etc/hosts
```

**Remote hostname (Let's Encrypt — free, trusted globally):**
```bash
sudo certbot certonly --standalone -d your-domain.company.com
# Then update nginx.conf ssl_certificate paths accordingly
```

**Certificate rotation reminder**: mkcert certs expire in 2 years; Let's Encrypt auto-renews via systemd timer.

### 4.5 Backup Strategy

```bash
# /opt/sap/docker/scripts/backup.sh (see Appendix D for full script)

# What's backed up:
#  1. $DATA_ROOT/warehouse/ + archive/ + exports/  (DuckLake parquet, serving DBs)
#  2. PostgreSQL full dump (pg_dumpall)
#  3. sap_sqlmesh_state named volume

# Schedule via cron:
# 0 2 * * *  root  /opt/sap/docker/scripts/backup.sh >> /var/log/sap-backup.log 2>&1

# Retention: 30 days local, then offload to S3/USB/NAS as budget allows
```

### 4.6 Connecting Superset to the Serving DB

After deploying, add the DuckDB data source in Superset:
1. Log in at `https://sales-analytics.local:8443`
2. Settings → Database Connections → + Database
3. Select DuckDB
4. Connection string: `duckdb:////app/data/warehouse/serving.db`
5. Enable: "Allow DML" (read-only is fine — don't enable)

Superset reads `serving.db` via the bind mount at `/app/data/` (read-only).
The file-swap sync (`ServingLayerSync`) does an atomic rename — Superset connections
that are mid-query get a clean `database is locked` error and retry. Zero data corruption.

### 4.7 SharePoint Source Sync

**Option A (Simplest): OneDrive/SharePoint desktop client on host**
The sync client writes Excel files to a local folder. Set `DATA_ROOT/source/` as the sync target.
No code change needed; the `new_file_sensor` picks up new files automatically.

**Option B: Microsoft Graph API (automated, no desktop client)**
Add a Dagster asset `sharepoint_sync` that uses `O365` Python library:
```python
# orchestration/assets/sharepoint_sync.py
from O365 import Account

@asset(group_name="ingestion")
def sharepoint_sync(context):
    account = Account(
        (os.getenv("AZURE_CLIENT_ID"), os.getenv("AZURE_CLIENT_SECRET")),
        auth_flow_type="credentials",
        tenant_id=os.getenv("AZURE_TENANT_ID"),
    )
    # Download new files to data/source/
    ...
```
Add `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID` to `.env.prod`.
Schedule before the 06:00 pipeline (`5 6 * * *`).

---

## 5. Phase 2: Hybrid Cloud Evolution (Month 2+)

### 5.1 Storage: MinIO → Real Cloud Object Storage

DuckLake writes Parquet files. The only change needed to migrate storage is the endpoint and credentials —
no SQL, no model changes.

**Step 1: Add MinIO to Compose** (S3-compatible, runs locally, zero cost):
```yaml
# Append to docker-compose.prod.yml
minio:
  image: minio/minio:RELEASE.2025-05-24T17-08-30Z
  container_name: sap-minio
  command: server /data --console-address ":9001"
  environment:
    MINIO_ROOT_USER: ${MINIO_USER}
    MINIO_ROOT_PASSWORD: ${MINIO_PASSWORD}
  volumes:
    - sap_minio_data:/data
  ports:
    - "127.0.0.1:9000:9000"   # S3 API
    - "127.0.0.1:9001:9001"   # Console UI
```

**Step 2: Update DuckLake storage path** in `sqlmesh/config.yaml`:
```yaml
variables:
  ducklake_catalog: "s3://sap-lake/catalog.ducklake"

# DuckDB S3 config injected via env vars:
# AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_ENDPOINT (minio host)
```

**Step 3 (cloud migration):** Change three env vars — no code touch:
```bash
# .env.prod
AWS_S3_ENDPOINT=s3.amazonaws.com          # or blob.core.windows.net for Azure
AWS_ACCESS_KEY_ID=<aws_key>
AWS_SECRET_ACCESS_KEY=<aws_secret>
DUCKLAKE_CATALOG=s3://your-prod-bucket/catalog.ducklake
```

### 5.2 SQLMesh State Migration (local SQLite → PostgreSQL)

When running on a remote VM or multi-node setup, use PostgreSQL for SQLMesh state too:
```yaml
# sqlmesh/config.yaml (phase 2)
gateways:
  prod:
    state_connection:
      type: postgres
      host: ${POSTGRES_HOST}
      port: 5432
      database: sqlmesh_state
      user: ${POSTGRES_USER}
      password: ${POSTGRES_PASSWORD}
```

### 5.3 Dagster Cloud Hybrid Migration

Minimal change process:
1. Create a Dagster Cloud account (free tier for evaluation)
2. In `dagster.yaml`, replace `storage:postgres` with the Dagster Cloud backend token
3. Run `dagster-cloud agent run` locally instead of `dagster-daemon run`
4. The Dagster Cloud UI replaces the self-hosted webserver; no nginx rule needed for Dagster

```yaml
# dagster.yaml (cloud hybrid)
dagster_cloud_api_token:
  env: DAGSTER_CLOUD_AGENT_TOKEN
deployment: prod
```

All pipeline code, sensors, schedules, and assets stay unchanged.

---

## 6. CI/CD Pipeline

```yaml
# .github/workflows/ci.yml
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }

      - name: Install
        run: pip install -e ".[dev]"

      - name: Lint
        run: ruff check .

      - name: Type check
        run: mypy ingestion/ serving/ orchestration/ shared/ --ignore-missing-imports

      - name: pytest
        run: pytest tests/ -v --tb=short

      - name: SQLMesh unit tests
        working-directory: sqlmesh
        run: sqlmesh test

      - name: Docker build check
        run: docker build -f docker/Dockerfile.app -t sap:ci-test .

  deploy:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: self-hosted    # GitHub Actions runner on the production host
    steps:
      - uses: actions/checkout@v4

      - name: Build and rolling restart
        env:
          BUILD_DATE: ${{ github.event.head_commit.timestamp }}
          GIT_COMMIT: ${{ github.sha }}
        run: |
          docker compose \
            --env-file /opt/sap/.env.prod \
            -f docker/docker-compose.prod.yml \
            build --no-cache --build-arg BUILD_DATE=$BUILD_DATE --build-arg GIT_COMMIT=$GIT_COMMIT
          # Rolling restart: stop old, start new — data containers (postgres, redis) restart only if image changed
          docker compose \
            --env-file /opt/sap/.env.prod \
            -f docker/docker-compose.prod.yml \
            up -d --force-recreate

      - name: Smoke test
        run: |
          sleep 45
          curl -sfk https://localhost/_stcore/health   || { echo "Streamlit down"; exit 1; }
          curl -sfk https://localhost:8443/health       || { echo "Superset down";  exit 1; }
          docker exec sap-dagster-daemon dagster-daemon liveness-check || { echo "Daemon down"; exit 1; }
```

---

## 7. Operational Runbook

### 7.1 Daily Checks (most automated via Dagster sensors)

```bash
# One-line status check — run any morning
docker compose -f docker/docker-compose.prod.yml ps --format "table {{.Name}}\t{{.Status}}"
```

Expected: all services show `Up (healthy)` or `Up`.

Dagster sensors handle:
- ✅ `new_file_sensor` — triggers pipeline on new files
- ✅ `sync_health_sensor` — alerts on failed sync / duration spike
- ✅ `prod_promotion_sensor` — enforces dev-first gate
- ✅ `data_quality_full_report` — writes JSON to `data/exports/quality_reports/`

### 7.2 Common Failure Modes and Fixes

| Symptom | Root cause | Fix |
|---|---|---|
| Pipeline stuck, no runs completing | Dagster daemon crashed | `docker restart sap-dagster-daemon` |
| Dead-letter files accumulating | Source Excel schema changed | Inspect `data/dead_letter/`, fix extractor, re-run |
| `assert_sales_data_is_fresh` audit fails | No new Excel files arrived | Check SharePoint sync; drop file manually into `data/source/` |
| Streamlit "cannot connect to database" | Serving sync didn't run | `docker exec sap-dagster-daemon python -m serving.cli sync --env prod` |
| Superset queries time out | Serving DB locked mid-swap | Wait 30s for atomic rename to complete, retry |
| PostgreSQL disk full | Run history accumulation | `docker exec sap-postgres psql -U $POSTGRES_USER -c "VACUUM ANALYZE;"` then `dagster instance purge` |
| Container keeps restarting | OOM or import error | `docker logs sap-<service> --tail 50` to diagnose |
| SQLMesh audit fails in prod | Bad data promotion from dev | `cd sqlmesh && sqlmesh plan prod --revert` to roll back |

### 7.3 Manual Operations

```bash
# Re-run failed pipeline
docker exec sap-dagster-webserver \
  dagster job execute -j daily_pipeline_job --env-file /opt/sap/.env.prod

# Promote dev → prod manually (bypasses prod_promotion_sensor)
docker exec sap-dagster-daemon \
  bash -c "cd /app/sqlmesh && sqlmesh plan prod --auto-apply"

# Sync serving DB (emergency / manual)
docker exec sap-dagster-daemon \
  python -m serving.cli sync --env prod

# Validate serving DB freshness
docker exec sap-dagster-daemon \
  python -m serving.cli validate --env prod

# Export to CSV (for Power BI / Excel users)
docker exec sap-dagster-daemon \
  python -m serving.cli export --env prod --csv

# Run all data quality audits explicitly
docker exec sap-dagster-daemon \
  bash -c "cd /app/sqlmesh && sqlmesh audit --env prod --start 2025-01-01"
```

### 7.4 Log Access

```bash
# All services, last 100 lines
docker compose -f docker/docker-compose.prod.yml logs --tail=100

# Follow dagster daemon (most useful for debugging)
docker compose -f docker/docker-compose.prod.yml logs -f dagster-daemon

# Structured log search (jq required)
docker compose -f docker/docker-compose.prod.yml logs --no-log-prefix dagster-daemon \
  | jq 'select(.level == "ERROR")'
```

### 7.5 Disaster Recovery

If the host disk fails completely:
1. Restore `$DATA_ROOT` from latest backup (`backup.sh` tar.zst)
2. Restore PostgreSQL: `zstd -d postgres.sql.zst | docker exec -i sap-postgres psql -U $POSTGRES_USER`
3. Restore SQLMesh state: extract `sqlmesh_state.tar.zst` into the named volume
4. `docker compose up -d`
5. Run `python -m serving.cli validate --env prod` — if serving DB is stale, re-run serving job

RTO (Recovery Time Objective): < 30 minutes from backup restoration.

---

## Appendix A — Key Config Files (See Repo)

| File | Purpose |
|---|---|
| `docker/docker-compose.prod.yml` | Full production stack definition |
| `docker/Dockerfile.app` | Single image for dagster-daemon, dagster-webserver, streamlit |
| `docker/dagster/dagster.yaml` | PostgreSQL backend, QueuedRunCoordinator |
| `docker/dagster/workspace.yaml` | Points Dagster at orchestration package |
| `docker/postgres/init.sql` | Creates `dagster` and `superset` databases |
| `docker/nginx/nginx.conf` | TLS, reverse proxy, BasicAuth for Dagster UI |
| `docker/superset/superset_config.py` | Redis cache, proxy fix, CSRF settings |
| `docker/scripts/backup.sh` | Nightly backup with 30-day rotation |
| `.env.prod.example` | Complete variable reference (no values) |

---

## Appendix B — Production Cold Start Procedure

```bash
# ── 0. Prerequisites ────────────────────────────────────────────────────
# Docker ≥ 24, Docker Compose ≥ 2.20, mkcert, git, Python 3.11

# ── 1. Deploy codebase ──────────────────────────────────────────────────
git clone <repo> /opt/sap
cd /opt/sap

# ── 2. Secrets ──────────────────────────────────────────────────────────
cp .env.prod.example .env.prod
# Edit .env.prod with generated secrets (see §3.1)
chmod 600 .env.prod

# ── 3. TLS certificates ─────────────────────────────────────────────────
mkcert -install
mkcert -cert-file docker/nginx/certs/local.crt \
       -key-file  docker/nginx/certs/local.key \
       sales-analytics.local localhost 127.0.0.1
echo "127.0.0.1 sales-analytics.local" | sudo tee -a /etc/hosts

# ── 4. Nginx BasicAuth (Dagster UI) ─────────────────────────────────────
htpasswd -c docker/nginx/.htpasswd dagster-admin

# ── 5. Data directory ───────────────────────────────────────────────────
mkdir -p /opt/sap/data/{source,input,archive,dead_letter,warehouse,exports}
mkdir -p /opt/sap/data/exports/{csv,parquet,quality_reports}

# ── 6. Start infrastructure first ───────────────────────────────────────
docker compose --env-file .env.prod \
  -f docker/docker-compose.prod.yml \
  up -d postgres redis
sleep 15  # Wait for pg to be ready

# ── 7. Initialize SQLMesh prod state ────────────────────────────────────
docker compose --env-file .env.prod \
  -f docker/docker-compose.prod.yml \
  run --rm dagster-daemon \
  bash -c "cd /app/sqlmesh && sqlmesh plan prod --start 2025-01-01 --auto-apply"

# ── 8. Start all services ────────────────────────────────────────────────
docker compose --env-file .env.prod \
  -f docker/docker-compose.prod.yml \
  up -d

# ── 9. Verify ───────────────────────────────────────────────────────────
sleep 30
docker compose --env-file .env.prod -f docker/docker-compose.prod.yml ps
curl -skf https://localhost/_stcore/health   && echo "✅ Streamlit"
curl -skf https://localhost:8443/health       && echo "✅ Superset"
curl -skf https://localhost:3443/server_info  && echo "✅ Dagster"

# ── 10. Schedule backup cron ────────────────────────────────────────────
echo "0 2 * * * root /opt/sap/docker/scripts/backup.sh >> /var/log/sap-backup.log 2>&1" \
  | sudo tee /etc/cron.d/sap-backup
```

---

## Appendix C — Minimum Hardware Requirements

| Tier | CPU | RAM | Disk | Cost |
|---|---|---|---|---|
| **Local minimum** | 4 cores | 8 GB | 100 GB SSD | Existing hardware |
| **Local recommended** | 8 cores | 16 GB | 500 GB SSD | Existing hardware |
| **Cloud VM (Phase 2b)** | 2 vCPU | 4 GB | 80 GB SSD | ~$30-35/month (t3.medium / B2s) |

DuckDB is in-process — it benefits heavily from available RAM for large datasets. Postgres and Redis are
lightweight. Superset + Celery worker are the most memory-hungry new services (~500 MB each).

---

*End of plan. All referenced config files are in the `docker/` directory of this repository.*
