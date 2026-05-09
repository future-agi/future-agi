# Installation

How to run Future AGI on your own infrastructure. This guide covers the supported deployment modes, every configurable knob, and the common issues people hit on first boot.

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
- [Email (SMTP)](#email-smtp)
- [Upgrading](#upgrading)
- [Backups](#backups)
- [Troubleshooting](#troubleshooting)
- [Production hardening](#production-hardening)

---

## Quick start

```bash
git clone https://github.com/future-agi/future-agi.git
cd future-agi
./bin/install
```

First boot pulls images from Docker Hub (~30 seconds on a fast connection). No source builds.

`bin/install` does three things:

1. Copies `.env.example` → `.env` if missing (compose's inlined defaults make even an empty `.env` valid for local use).
2. Brings up the stack with `docker compose up -d`.
3. Polls `http://localhost:8000/health/` until the backend is ready, then prompts you to create your first user.

For production, do not rely on local-only defaults. See [`deploy/README.md`](deploy/README.md).

Useful flags:

| Flag | What it does |
|---|---|
| `--skip-user-creation` | Skip the first-user prompt. Run the `create_user` command later. |
| `--no-up` | Bootstrap `.env` only; don't start the stack. |

When the backend logs `Application startup complete`, open:

- **Frontend**: <http://localhost:3000>
- **Backend API**: <http://localhost:8000>

### Create your first account

If you skipped the prompt at install time, create the admin account via the CLI:

```bash
docker exec -it futureagi-backend-1 python manage.py create_user
```

You will be prompted for your email, full name, and password. Then log in at <http://localhost:3000>.

To pass credentials non-interactively (useful for automated setups):

```bash
docker exec futureagi-backend-1 python manage.py create_user \
  --email you@example.com \
  --name "Your Name" \
  --password yourpassword
```

> **Team invites and password resets require email (SMTP).** See the [Email configuration](#email-smtp) section below for setup. Mailgun offers a free tier (100 emails/day) that works well for small self-hosted deployments.

To stop everything: `./bin/uninstall` (or `docker compose down`). Data persists in named volumes across restarts.

To wipe all data: `./bin/uninstall --wipe-data` (or `docker compose down -v`).

To fully remove the install (containers, volumes, `.env`, built images): `./bin/uninstall --purge`.

### Without the installer

If you'd rather run the steps by hand:

```bash
cp .env.example .env       # optional — empty .env works fine for local
docker compose up
```

---

## Prerequisites

| Requirement | Minimum | Notes |
|---|---|---|
| Docker Engine | 24.0+ | Docker Desktop on Mac/Windows, or native Docker on Linux |
| Docker Compose | v2.20+ | `docker compose version` should print v2.x |
| RAM | 8 GB | 16 GB recommended (ClickHouse and the worker each hold ~1 GB) |
| Disk | 20 GB free | Image pulls are ~3 GB; data grows from there |
| CPU | 4 cores | — |
| Platform | `privileged: true` supported | `code-executor` needs it — won't run on Fargate, Cloud Run, or some PaaS |

On Docker Desktop for Mac, give Docker at least **8 GB RAM** and **64 GB disk** under Settings → Resources. The defaults are often too small.

---

## Deployment modes

Three compose files at the repo root. Pick one or compose them with `-f`.

### Mode 1 — OSS stack

The default Compose stack runs frontend, backend, worker, agentcc-gateway, serving, code-executor, postgres, clickhouse, redis, minio, rabbitmq, and temporal from published images.

```bash
docker compose up                                # foreground
docker compose up -d                             # detached
docker compose ps
docker compose logs -f backend
```

Binds the frontend on `0.0.0.0:3000`; data stores stay on `127.0.0.1`. Put a reverse proxy in front of the frontend for HTTPS in any non-laptop deployment.

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
  docker compose -f docker-compose.frontend.yml up -d
```

Or set `VITE_HOST_API` in `.env` and run without the inline variable. Restart the container to pick up changes — no rebuild required (the entrypoint regenerates `/config.js` from `VITE_HOST_API` on each start).

---

## Configuration

### The `.env` file

`docker compose` automatically loads `.env` from the directory where you run it. Start from the example:

```bash
cp .env.example .env
```

Every knob in the compose file has a sensible local default, so the stack boots against an empty `.env`. For production, see [`deploy/README.md`](deploy/README.md) — the production overlay re-binds these with `${VAR:?error}` guards and refuses to boot on dev fallbacks.

### Optional values

Only needed if you enable the corresponding feature:

| Variable | Used by |
|---|---|
| `OPENAI_API_KEY` | Evaluations, agent loops, text simulation |
| `ANTHROPIC_API_KEY` | Same as above |
| `GOOGLE_API_KEY` | Gemini models |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Bedrock models, S3 object storage |
| `FUTURE_AGI_CLOUD_API_KEY` | Future AGI Cloud API features. Leave blank to run fully offline. |

### Ports reference

All ports are configurable via `.env`. Defaults:

| Service | Port | URL |
|---|---|---|
| Frontend | `3000` | <http://localhost:3000> |
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
To run two stacks side-by-side, copy `.env` to `.env.stackB`, change every port, and `docker compose --env-file .env.stackB -p stackb up`.

---

## Services and what they do

### Application services

| Service | Purpose |
|---|---|
| `frontend` | React SPA served by nginx. UI for traces, evals, datasets, playground. |
| `backend` | Django API. Serves REST + gRPC + WebSockets. Reads/writes Postgres + ClickHouse + Redis + MinIO. |
| `worker` | Single Temporal worker polling all queues. Replaced by six per-queue workers in dev mode. |
| `agentcc-gateway` | Go-based LLM proxy. Routes calls to OpenAI, Anthropic, Gemini, Bedrock, Vertex. Handles retries, rate limits, logging. |
| `serving` | Python service for embeddings and small model inference. |
| `code-executor` | nsjail-sandboxed Python/JS code runner for evaluation code. **Requires `privileged: true`.** |

### Data stores

| Service | Role |
|---|---|
| `postgres` | Primary transactional store (users, traces, datasets, evals, prompts, annotations). |
| `clickhouse` | Analytics store for traces, spans, dashboards, and evaluation queries. |
| `redis` | Cache, rate limits, Celery/Django cache, WebSocket pub/sub. |
| `minio` | S3-compatible object storage (uploaded files, eval artifacts). In production, swap for real S3 by setting `S3_ENDPOINT_URL` to an AWS endpoint. **Note:** the backend uses `S3_ENDPOINT_URL` (internal Docker hostname) to talk to MinIO, but URLs returned to the browser use `MINIO_URL` (defaults to `http://localhost:9005`). If you access the UI from anywhere other than the host machine — e.g. another machine on your LAN, a remote VM, or a domain name — set `MINIO_URL` in `.env` to a URL the browser can reach (e.g. `http://your-host.example.com:9005`). |

### Workflow engine

| Service | Role |
|---|---|
| `temporal` | Durable workflow server (auto-setup). Shares the main Postgres. |

## Configuring LLM providers

The gateway ships with `config.example.yaml`, enabling OpenAI by default. To enable more providers:

1. Copy the example:
   ```bash
   cp agentcc-gateway/config.example.yaml \
      agentcc-gateway/config.yaml
   ```
2. Uncomment the providers you want (Anthropic, Gemini, Bedrock, Vertex).
3. Set `AGENTCC_CONFIG_PATH=agentcc-gateway/config.yaml` in `.env`.
4. Set the matching `*_API_KEY` env vars in `.env`.
5. Restart: `docker compose up -d --force-recreate agentcc-gateway`.

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

Set `GOOGLE_ACCESS_TOKEN` in `.env` and rotate it via a sidecar that calls `gcloud auth print-access-token`. **Do not mount `Vertex_AI_Creds.json` into the container** — it's covered by `.gitignore` but mounting it is still a bad habit. If a code path elsewhere in the platform genuinely needs the JSON keyfile (`GOOGLE_APPLICATION_CREDENTIALS`), arrange the volume mount yourself in a local compose override — never commit the file or bake it into a shared image.

---

## Email (SMTP)

Email is **optional for the initial setup** — you can create your first account via the CLI (see [Create your first account](#create-your-first-account)). However, email is required for:

- **Team invites** — invite links are sent by email
- **Password resets** — reset tokens are delivered by email

### Configuring Mailgun (recommended, free tier available)

[Mailgun](https://www.mailgun.com/) offers 100 emails/day free. Sign up, add a sending domain, and copy your API key.

Add to `.env`:

```bash
MAILGUN_API_KEY=your-mailgun-api-key
MAILGUN_SENDER_DOMAIN=mail.yourdomain.com
DEFAULT_FROM_EMAIL=Future AGI <noreply@mail.yourdomain.com>
```

Restart the backend: `docker compose up -d --force-recreate backend worker`.

### Password reset without email

If SMTP is not configured and a user needs a password reset, a shell admin can generate the reset link directly:

```bash
docker exec -it futureagi-backend-1 python manage.py shell
```

```python
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from accounts.models import User

user = User.objects.get(email="user@example.com")
uid = urlsafe_base64_encode(force_bytes(user.pk))
token = default_token_generator.make_token(user)
print(f"http://localhost:3000/auth/reset-password/{uid}/{token}/")
```

Share the printed URL with the user out-of-band (Slack, email, etc.).

---

## Stack mode

The standard Compose stack runs the full OSS application path from published images:

| Mode | Containers | What you get | When to use |
|---|---|---|---|
| **standard** | ~12 | Frontend, backend, worker, agentcc-gateway, serving, code-executor, postgres, clickhouse, redis, minio, rabbitmq, temporal | Local evaluation, team installs, and VM-based self-hosting. |

Start it with:

```bash
docker compose up
```

## Upgrading

Each image is independently versioned. Bump the relevant variable(s) in `.env` (`FUTURE_AGI_VERSION` for backend + worker, `FRONTEND_VERSION`, `AGENTCC_GATEWAY_VERSION`, `SERVING_VERSION`, `CODE_EXECUTOR_VERSION`), then:

```bash
docker compose pull
docker compose up -d
```

Backend migrations run automatically on startup. Downtime is ~30 seconds for the backend restart. Workers restart independently. To roll back, set the bumped variable(s) to the previous tag and re-run the same two commands.

## Backups

Named Docker volumes hold all state:

```bash
docker volume ls | grep future-agi
# future-agi_postgres-data
# future-agi_clickhouse-data
# future-agi_redis-data
# future-agi_minio-data
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
You're on a split-domain deployment without `VITE_HOST_API` set. Set it in `.env` (or your production env file) to your absolute backend URL and restart the frontend container:
```bash
echo "VITE_HOST_API=https://api.example.com" >> .env
docker compose up -d frontend
```
The container's entrypoint regenerates `/config.js` on each start, so no rebuild is needed.

### `code-executor` crashes with `clone: Operation not permitted`
The host kernel or container platform disallows `privileged: true` (Fargate, Cloud Run, some Kubernetes policies). Either run on a platform that allows privileged containers (EC2, GKE with privileged enabled, bare-metal) or disable code evaluation features.

### `temporal-server` keeps restarting
Postgres connection is the usual cause. Check `docker compose logs postgres` for OOM. Raise Docker Desktop's RAM to 8 GB+.

---

## Production hardening

See [`deploy/README.md`](deploy/README.md) for the production deployment guide. It covers the production overlay (`deploy/docker-compose.production.yml`), required secrets, deployment topologies, reverse-proxy/TLS, backups, upgrades, and the pre-flight checklist.

---

Questions, bugs, or contributions: <https://github.com/future-agi/future-agi/issues>.
