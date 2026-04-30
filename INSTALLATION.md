# Installation

How to run Future AGI on your own infrastructure. This guide covers the three supported deployment modes, every configurable knob, and the common issues people hit on first boot.

If you just want to try it on your laptop, jump to [Quick start](#quick-start).

---

## Contents

- [Quick start](#quick-start)
- [Prerequisites](#prerequisites)
- [Deployment modes](#deployment-modes)
  - [Full OSS stack (default)](#mode-1-full-oss-stack)
  - [Development mode (hot reload)](#mode-2-development-mode)
  - [Frontend-only deploy](#mode-3-frontend-only)
- [Configuration](#configuration)
  - [The `.env` file](#the-env-file)
  - [Secrets that must be changed](#secrets-that-must-be-changed)
  - [Ports reference](#ports-reference)
- [Services and what they do](#services-and-what-they-do)
- [Configuring LLM providers](#configuring-llm-providers)
- [PeerDB mirror setup](#peerdb-mirror-setup)
- [Upgrading](#upgrading)
- [Backups](#backups)
- [Troubleshooting](#troubleshooting)
- [Production hardening](#production-hardening)

---

## Quick start

```bash
git clone https://github.com/future-agi/future-agi.git
cd future-agi
cp .env.example .env
docker compose up
```

First boot builds the backend image from source (~10–15 minutes on a modern laptop). Subsequent boots take under 30 seconds.

When the backend logs `Application startup complete`, open:

- **Frontend**: <http://localhost:3031>
- **Backend API**: <http://localhost:8000>
- **PeerDB UI**: <http://localhost:3001> (user/pass: `peerdb` / `peerdb`)

To stop everything: `docker compose down`. Data persists in named volumes across restarts.
To wipe all data: `docker compose down -v`.

---

## Prerequisites

| Requirement | Minimum | Notes |
|---|---|---|
| Docker Engine | 24.0+ | Docker Desktop on Mac/Windows, or native Docker on Linux |
| Docker Compose | v2.20+ | `docker compose version` should print v2.x |
| RAM | 8 GB | 16 GB recommended (ClickHouse and the worker each hold ~1 GB) |
| Disk | 20 GB free | Initial image builds are ~6 GB; data grows from there |
| CPU | 4 cores | 8+ cores materially speeds up the initial build |
| Platform | `privileged: true` supported | `code-executor` needs it — won't run on Fargate, Cloud Run, or some PaaS |

On Docker Desktop for Mac, give Docker at least **8 GB RAM** and **64 GB disk** under Settings → Resources. The defaults are often too small.

---

## Deployment modes

Three compose files at the repo root. Pick one or compose them with `-f`.

### Mode 1 — Full OSS stack

The default. 21 services including frontend, backend, gateway, worker, serving, code-executor, databases, Temporal, and PeerDB CDC.

```bash
docker compose up          # foreground
docker compose up -d       # detached
docker compose ps          # check status
docker compose logs -f backend   # tail a service's logs
```

Use this for self-hosted evaluation or production. Binds the frontend publicly on `0.0.0.0:3031` and keeps all data stores on `127.0.0.1` so only the host can reach them. Put a reverse proxy (nginx, Caddy, Traefik) in front of the frontend for HTTPS.

### Mode 2 — Development mode

For Future AGI engineers or contributors hacking on the code. Layers `docker-compose.dev.yml` on top of the base compose.

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

Adds:

- **Hot reload** — `./futureagi` is volume-mounted into the backend and workers, so Python changes reload without a rebuild.
- **Per-queue workers** — six Temporal workers (`default`, `tasks_s`, `tasks_l`, `tasks_xl`, `trace_ingestion`, `agent_compass`) instead of one all-queue worker, mirroring production topology.
- **Public DB ports** — Postgres `5432`, ClickHouse `8123/9000`, Redis `6379`, MinIO `9000/9001`, Temporal `7233` all bind on `0.0.0.0` so DBeaver / DataGrip / `psql` on the host can connect.
- **Temporal UI + admin tools** — workflow inspection at <http://localhost:8085>.
- **`FAST_STARTUP=true`** — skips migrations on every restart (run them manually with `docker compose exec backend python manage.py migrate`).

The single all-queue `worker` service from Mode 1 is disabled in dev mode (moved behind the `oss-only` profile) so you don't get duplicate workers polling the same queues.

### Mode 3 — Frontend-only

For users who run the backend elsewhere (a VM, Future AGI Cloud, another Compose project, a Kubernetes cluster) and only want a local UI container.

```bash
VITE_HOST_API=https://api.your-backend.example.com \
  docker compose -f docker-compose.frontend.yml up --build
```

Or set `VITE_HOST_API` in `.env` and run without the inline variable. Since Vite bakes the API URL into the JS bundle at build time, changing `VITE_HOST_API` requires a rebuild:

```bash
docker compose -f docker-compose.frontend.yml build --no-cache frontend
```

---

## Configuration

### The `.env` file

`docker compose` automatically loads `.env` from the directory where you run it. Start from the example:

```bash
cp .env.example .env
```

Every knob in the compose file has a sensible default, so the stack will boot against an unedited `.env.example`. In production you must change the `CHANGEME` values (see below).

### Secrets that must be changed

Before running in anything other than a local evaluation:

| Variable | Generate with | Used by |
|---|---|---|
| `SECRET_KEY` | `openssl rand -hex 32` | Django — session signing, CSRF, password reset tokens |
| `PG_PASSWORD` | `openssl rand -base64 24` | Postgres auth |
| `MINIO_ROOT_PASSWORD` | `openssl rand -base64 24` | Object storage auth |
| `AGENTCC_INTERNAL_API_KEY` | `openssl rand -hex 32` | Shared secret between backend and gateway |

Optional — only needed if you enable the corresponding feature:

| Variable | Used by |
|---|---|
| `OPENAI_API_KEY` | Evaluations, agent loops, text simulation |
| `ANTHROPIC_API_KEY` | Same as above |
| `GOOGLE_API_KEY` | Gemini models |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Bedrock models, S3 object storage (production) |
| `FUTURE_AGI_CLOUD_API_KEY` | EE-tier Cloud API features (Falcon AI, Turing, Protect, insights). Leave blank on OSS. |

### Ports reference

All ports are configurable via `.env`. Defaults:

| Service | Port | URL |
|---|---|---|
| Frontend | `3031` | <http://localhost:3031> |
| Backend API | `8000` | <http://localhost:8000> |
| Gateway (LLM proxy) | `8090` | internal only by default |
| Model serving (embeddings) | `8080` | internal only by default |
| Code executor | `8060` | internal only by default |
| Postgres | `5432` | `127.0.0.1` — dev mode only: `0.0.0.0` |
| ClickHouse HTTP | `8123` | same |
| ClickHouse TCP | `9000` | same |
| Redis | `6379` | same |
| MinIO S3 API | `9000` | same |
| MinIO console | `9001` | same |
| Temporal gRPC | `7233` | same |
| Temporal UI | `8085` | dev mode only |
| PeerDB server | `9900` | `127.0.0.1` |
| PeerDB UI | `3001` | <http://localhost:3001> |

To run two stacks side-by-side, copy `.env` to `.env.stackB`, change every port, and `docker compose --env-file .env.stackB -p stackb up`.

---

## Services and what they do

### Application services

| Service | Purpose |
|---|---|
| `frontend` | React SPA served by nginx. UI for traces, evals, datasets, playground. |
| `backend` | Django API. Serves REST + gRPC + WebSockets. Reads/writes Postgres + ClickHouse + Redis + MinIO. |
| `worker` | Single Temporal worker polling all queues. Replaced by six per-queue workers in dev mode. |
| `gateway` | Go-based LLM proxy (Prism). Routes calls to OpenAI, Anthropic, Gemini, Bedrock, Vertex. Handles retries, rate limits, logging. |
| `serving` | Python service for embeddings and small model inference. |
| `code-executor` | nsjail-sandboxed Python/JS code runner for evaluation code. **Requires `privileged: true`.** |

### Data stores

| Service | Role |
|---|---|
| `postgres` | Primary transactional store (users, traces, datasets, evals, prompts, annotations). `wal_level=logical` enabled for CDC. |
| `clickhouse` | Analytics store. Traces/spans replicated here via PeerDB for fast querying. |
| `redis` | Cache, rate limits, Celery/Django cache, WebSocket pub/sub. |
| `minio` | S3-compatible object storage (uploaded files, eval artifacts). In production, swap for real S3 by setting `S3_ENDPOINT_URL` to an AWS endpoint. |

### Workflow engine

| Service | Role |
|---|---|
| `temporal` | Durable workflow server (auto-setup). Shares the main Postgres. |

### CDC (PeerDB)

| Service | Role |
|---|---|
| `peerdb-catalog` | PeerDB's own Postgres (mirror definitions, state). |
| `peerdb-temporal` | PeerDB's own Temporal cluster (mirror orchestration). Independent of the app's Temporal. |
| `peerdb-minio` | PeerDB's own MinIO (staging for ClickHouse loads). |
| `peerdb-flow-api` / `peerdb-flow-worker` / `peerdb-flow-snapshot-worker` | Mirror execution. |
| `peerdb-server` / `peerdb-ui` | PeerDB API (port 9900) and web UI (port 3001). |
| `peerdb-temporal-init` / `peerdb-init` | One-shot initialization: registers the `MirrorName` search attribute and creates mirrors from `scripts/peerdb-setup-mirrors.sh`. |

---

## Configuring LLM providers

The gateway ships with `config.example.yaml`, enabling OpenAI by default. To enable more providers:

1. Copy the example:
   ```bash
   cp futureagi/agentcc-gateway/config.example.yaml \
      futureagi/agentcc-gateway/config.yaml
   ```
2. Uncomment the providers you want (Anthropic, Gemini, Bedrock, Vertex).
3. Update the gateway mount in `docker-compose.yml` to point at `config.yaml` instead of `config.example.yaml`:
   ```yaml
   gateway:
     volumes:
       - ./futureagi/agentcc-gateway/config.yaml:/app/config.yaml:ro
   ```
4. Set the matching `*_API_KEY` env vars in `.env`.
5. Restart: `docker compose up -d --force-recreate gateway`.

Your `config.yaml` is gitignored by default — the example file uses `${VAR}` interpolation so the real key never has to live in source. Treat the file as a secret regardless.

### Vertex AI

Vertex needs a Bearer token from a GCP service account, not an API key. The recommended pattern:

```yaml
vertex:
  base_url: "https://us-central1-aiplatform.googleapis.com"
  api_key: "${GOOGLE_ACCESS_TOKEN}"
  api_format: "gemini"
  headers:
    x-gcp-project: "${GCP_PROJECT_ID}"
    x-gcp-location: "us-central1"
```

Rotate `GOOGLE_ACCESS_TOKEN` via a sidecar that calls `gcloud auth print-access-token`. **Do not mount `Vertex_AI_Creds.json` into the container** — it's covered by `.gitignore` but mounting it is still a bad habit.

---

## PeerDB mirror setup

PeerDB replicates your app's Postgres tables into ClickHouse so the analytics UI stays fast. Mirrors are registered once, then run continuously.

On first boot the `peerdb-init` service will attempt to create mirrors from `futureagi/scripts/peerdb-setup-mirrors.sh`. If the backend hasn't migrated yet, mirror creation will fail (the source tables don't exist). In that case:

1. Wait for `docker compose logs backend` to show migrations complete.
2. Re-run the init:
   ```bash
   docker compose run --rm peerdb-init bash /setup.sh
   ```

Or inspect and create mirrors manually at <http://localhost:3001>.

---

## Upgrading

```bash
cd future-agi
git pull
docker compose build
docker compose up -d
```

Backend migrations run automatically on startup. Downtime is ~30 seconds for the backend restart. Workers restart independently.

If a release note mentions breaking changes to PeerDB mirrors, re-run `docker compose run --rm peerdb-init bash /setup.sh`.

---

## Backups

Named Docker volumes hold all state:

```bash
docker volume ls | grep future-agi
# future-agi_postgres-data
# future-agi_clickhouse-data
# future-agi_redis-data
# future-agi_minio-data
# future-agi_peerdb-catalog-data
# future-agi_peerdb-minio-data
```

To back up Postgres:

```bash
docker compose exec postgres \
  pg_dump -U futureagi -d futureagi --format=custom \
  > backup-$(date +%F).dump
```

To restore:

```bash
docker compose exec -T postgres \
  pg_restore -U futureagi -d futureagi --clean --if-exists \
  < backup-2026-04-22.dump
```

For ClickHouse, prefer `BACKUP TABLE ... TO S3(...)` rather than file-level copies. See ClickHouse's [Backup and Restore docs](https://clickhouse.com/docs/en/operations/backup).

MinIO can be mirrored to any S3 endpoint via `mc mirror`.

---

## Troubleshooting

### `Cannot connect to the Docker daemon`
Docker isn't running. Start Docker Desktop (Mac/Windows) or `sudo systemctl start docker` (Linux).

### `ERROR: You don't have enough free space in /var/cache/apt/archives/`
Docker Desktop's virtual disk is full. Either:
- Settings → Resources → Disk image size — raise to 100 GB+.
- Clean up: `docker system prune -af && docker builder prune -af`.

### `ports are not available: exposing port ... address already in use`
Another process is using that port. Either kill it or override in `.env`:
```
FRONTEND_PORT=3100
BACKEND_PORT=8100
```

### Backend logs `FATAL: password authentication failed for user "futureagi"`
You changed `PG_PASSWORD` after the volume was created. Postgres initializes the password on first boot only. Either:
- Revert `PG_PASSWORD` to the original, or
- Wipe and reinitialize: `docker compose down -v` (⚠ destroys all data).

### Frontend loads but API calls fail with CORS errors
The frontend bundle was built with a `VITE_HOST_API` that doesn't match your current backend URL. Rebuild:
```bash
docker compose build --no-cache frontend
docker compose up -d frontend
```

### `code-executor` crashes with `clone: Operation not permitted`
The host kernel or container platform disallows `privileged: true` (Fargate, Cloud Run, some Kubernetes policies). Either run on a platform that allows privileged containers (EC2, GKE with privileged enabled, bare-metal) or disable code evaluation features.

### PeerDB mirrors show "not started"
Source tables don't exist yet. Let the backend finish migrations, then:
```bash
docker compose run --rm peerdb-init bash /setup.sh
```

### Build hangs on `uv pip install`
Slow mirror or rate-limited PyPI. Retry after 60 seconds, or set `UV_TIMEOUT=1200` in the Dockerfile.

### `temporal-server` keeps restarting
Postgres connection is the usual cause. Check `docker compose logs postgres` for OOM. Raise Docker Desktop's RAM to 8 GB+.

---

## Production hardening

The defaults are tuned for development. Before putting this in front of real users:

1. **Change every `CHANGEME` in `.env`.** At minimum: `SECRET_KEY`, `PG_PASSWORD`, `MINIO_ROOT_PASSWORD`, `AGENTCC_INTERNAL_API_KEY`.
2. **Use real S3, not MinIO.** Set `S3_ENDPOINT_URL=https://s3.amazonaws.com`, provide AWS credentials, drop the `minio` service.
3. **Use managed Postgres / ClickHouse / Redis** for production. Point the env vars at them, drop the compose services.
4. **Set `ENV_TYPE=prod`** — enables gunicorn-style process counts, disables debug output, runs `check --deploy`.
5. **Set `FAST_STARTUP=false`** so migrations and cache tables run on every deploy.
6. **Put HTTPS in front.** Caddy, nginx, Traefik, or an ALB. Terminate TLS at the proxy; the frontend and backend do not speak TLS directly.
7. **Back up Postgres and ClickHouse on a schedule.** See [Backups](#backups).
8. **Monitor**. The backend emits Prometheus metrics at `/metrics`; add a scraper.

---

Questions, bugs, or contributions: <https://github.com/future-agi/future-agi/issues>.
