# Production deployment

This directory holds the production overlay for self-hosted Future AGI. The base `docker-compose.yml` at the repo root is geared for local evaluation with safe defaults; this overlay re-binds required secrets with `${VAR:?error}` guards so compose refuses to boot on dev fallbacks.

## Quickstart

First prepare configuration without starting services. The script preserves an
existing environment file byte-for-byte; it never regenerates its credentials.
For a new file it preserves explicitly supplied application secrets, generating
only missing ones. Catalog reader/writer passwords and all seven reviewed
application/collector/runner image versions must be supplied explicitly.
For retained data with a missing environment file, restore the original configuration
instead of generating new application or catalog credentials.

```bash
./deploy/setup.sh --skip-up
```

After the initialization prerequisite below has been independently satisfied:

```bash
./deploy/setup.sh --confirm-initialized
```

`--confirm-initialized` is an operator acknowledgement, not proof of initialization.
The existing check-only startup jobs still validate the actual schema/mirror state.
`--non-interactive` preserves an existing file; for a new one it requires explicit
`FRONTEND_URL`, `VITE_HOST_API`, all image versions listed below and both catalog-password
inputs. Missing required input or closed stdin fails instead of looping. Optional
provider-key prompts disable terminal echo; supplied provider keys are preserved. Supply
secrets through a protected environment/file, not command-line arguments or logs.
New prompted values are single-line; existing operator-managed dotenv files are
validated by Compose without being shell-sourced or rewritten.
Setup clears ambient application/catalog/provider secret and credential-path
overrides before Compose reads the file, so an export cannot silently replace an
installed credential. New supplied values are saved with Compose-safe quoting
(including dollar signs, quotes and trailing backslashes). Update
missing entries explicitly with the existing values; do not regenerate the file.

Manual flow (only after the same prerequisite; do not copy over an existing file):

```bash
cp deploy/.env.production.example deploy/.env.production
# fill in REQUIRED values; use the installed credentials for retained data
docker compose --env-file deploy/.env.production \
  -f docker-compose.yml -f deploy/docker-compose.production.yml config --quiet
docker compose --env-file deploy/.env.production \
  -f docker-compose.yml -f deploy/docker-compose.production.yml pull
docker compose --env-file deploy/.env.production \
  -f docker-compose.yml -f deploy/docker-compose.production.yml up -d --no-build --wait --wait-timeout 1200
```

If any required value is empty, compose exits with `must be set for production` and names the missing var.

## Prerequisites

- Docker Engine 24.0+ and Docker Compose v2.24+
- A reverse proxy that terminates TLS in front of the frontend (Caddy / nginx / Traefik / ALB)
- (Optional) Managed Postgres and S3-compatible object store if you don't want the bundled `postgres` / `minio` containers
- 16 GB RAM minimum on the host (8 GB is OK for smoke tests; ClickHouse and the worker each hold ~1 GB)

### Required initialization boundary

Production is **check-only**, not a fresh-database installer. Before boot, a
separately approved initialization/upgrade must have established current PostgreSQL
migrations, native ClickHouse objects, the two isolated observed indexes and their
reader/writer grants, and compatible PeerDB namespace/peers/mirrors. All source and
native database routing must match. See [the native bootstrap contract](../fi-collector/PROPERTY_CATALOG_OSS.md).

The overlay runs `migrate --check --noinput`, observed-index `--check`, and native/
CDC/PeerDB checks without `--apply`. Missing or incompatible state must block boot;
a compatible in-progress snapshot has a bounded readiness wait. Neither setup nor
this guide runs initialization, replaces mirrors, rewrites sources, or grants
migration authority. Do not remove these guards or run the mutating root-only
Compose stack as a production workaround. Retain partial state after a failed check
and review the exact failed job before explicitly resuming.

### Performing that initialization (first install)

The prohibition above is on using the mutating root stack as a *workaround* — on
letting `up` apply schema implicitly. It is not a prohibition on initializing at
all. A first install has to run these jobs once, deliberately, one at a time,
reviewing each before the next.

Run them against the production env file, overriding only the command so each job
applies instead of checking. `run --rm` starts one job and nothing else; it never
brings up the application.

```bash
cd <repo root>
COMPOSE="docker compose --env-file deploy/.env.production \
  -f docker-compose.yml -f deploy/docker-compose.production.yml"

# 1. PostgreSQL schema.
$COMPOSE run --rm --entrypoint python postgres-schema-bootstrap \
  manage.py migrate --noinput

# 2. Native ClickHouse objects.
$COMPOSE run --rm clickhouse-native-bootstrap --phase native --apply

# 3. PeerDB Temporal namespace, then peers and mirrors.
$COMPOSE run --rm peerdb-temporal-init --apply
$COMPOSE run --rm peerdb-init --apply

# 4. CDC-derived objects, once the mirrors above report ready.
$COMPOSE run --rm clickhouse-cdc-bootstrap \
  --phase cdc --apply --wait-for-mirrors --timeout 900

# 5. The two observed indexes and their reader/writer grants.
#    This job takes no --apply flag: the script provisions when given NO
#    arguments and only validates when given --check, and the production
#    overlay pins it to --check. Clearing the command is what makes it apply.
#    It CREATEs a database, tables and two roles, so it needs an administrative
#    ClickHouse login -- the overlay's own CLICKHOUSE_USER is the read-only
#    observed_catalog_reader and cannot provision. Supply your admin credential:
$COMPOSE run --rm \
  -e CLICKHOUSE_USER=<clickhouse admin user> \
  -e CLICKHOUSE_PASSWORD=<clickhouse admin password> \
  --entrypoint /bin/sh property-catalog-clickhouse-bootstrap \
  /bootstrap/bootstrap_clickhouse.sh
```

The writer and reader passwords this step installs come from
`PROPERTY_CATALOG_CONSUMER_PASSWORD` and `PROPERTY_CATALOG_API_PASSWORD` in your
env file. They are installed once and never rotated by setup, so for a retained
installation reuse the exact values already in place rather than generating new
ones.

Order matters: PostgreSQL migrations, then native ClickHouse tables, then the
PeerDB snapshot/CDC pair, then the observed indexes. Step 4 depends on step 3's
mirrors existing, which is why it waits rather than assuming.

Then boot normally. From that point the overlay is check-only for the lifetime of
the install, and `--confirm-initialized` is your acknowledgement that the steps
above were completed and reviewed:

```bash
./deploy/setup.sh --confirm-initialized
```

Upgrades re-run the same jobs with the same commands. They are idempotent — every
one is a no-op against current state — so a stalled upgrade can be resumed from the
job that failed rather than restarted from step 1.

## 1. Generate secrets

```bash
SECRET_KEY=$(openssl rand -hex 32)
AGENTCC_INTERNAL_API_KEY=$(openssl rand -hex 32)
AGENTCC_ADMIN_TOKEN=$(openssl rand -hex 32)
PG_PASSWORD=$(openssl rand -hex 16)
MINIO_ROOT_PASSWORD=$(openssl rand -hex 16)
```

For a new installation, paste each into `deploy/.env.production`. For retained
installations, reuse the original values. Also supply
`PROPERTY_CATALOG_API_PASSWORD` and `PROPERTY_CATALOG_CONSUMER_PASSWORD` matching
the separately provisioned `observed_catalog_reader`/`observed_catalog_writer`.
Setup never generates or rotates catalog passwords; changing an env value does not
change an installed ClickHouse user's password.

## 2. Pin a release

Each image is independently versioned. Include the collector release in `.env.production`:

| Variable | Image |
|---|---|
| `FUTURE_AGI_VERSION` | `futureagi/future-agi` (backend + worker) |
| `FRONTEND_VERSION` | `futureagi/frontend` |
| `FI_COLLECTOR_VERSION` | `futureagi/fi-collector` (collector, consumer and packaged backfill) |
| `AGENTCC_GATEWAY_VERSION` | `futureagi/agentcc-gateway` |
| `SERVING_VERSION` | `futureagi/serving` |
| `CODE_EXECUTOR_VERSION` | `futureagi/code-executor` |
| `SIMULATION_RUNNER_VERSION` | `futureagi/future-agi-simulation-runner` (separate SDK worker image) |

Use reviewed release tags and record their verified registry digests, source SHAs
and CPU architecture manifests; a version-looking tag alone is not immutable proof.
None of these image variables has a production fallback; missing/empty pins fail
configuration, including the simulation runner pin before its profile is enabled.
The backend pin covers bootstrap and ordinary workers; the SDK worker retains its
separate runner pin. Setup rejects collector `local`/`latest`, both
collector services select the same image, and the production overlay removes their
inherited build configuration. `up --no-build` additionally forbids source builds.
An explicitly verified `tag@sha256:<digest>` version suffix can pin image content;
no example tag or digest here is a qualified release. Backend, all applicable
workers and frontend must match the reviewed source; unchanged dependencies need
not be rebuilt. Keep these receipts for both fresh and retained-volume rehearsals.

The existing `FI_OBSERVED_CATALOG_MAX_KEYS_PER_SPAN` (default 128) and
`FI_OBSERVED_CATALOG_MAX_ARRAY_MEMBERS_PER_SPAN` (default 256) apply equally to
the collector and the packaged backfill invoked through the consumer service.
Set the same reviewed values in this env file; do not override them independently
for a repair run. These are extraction budgets, not tenant/activation settings.

## 3. Boot

```bash
docker compose --env-file deploy/.env.production \
  -f docker-compose.yml -f deploy/docker-compose.production.yml \
  pull
docker compose --env-file deploy/.env.production \
  -f docker-compose.yml -f deploy/docker-compose.production.yml \
  up -d --no-build --wait --wait-timeout 1200
```

Verify:

```bash
docker compose ps
curl -fsS http://localhost:3000/ > /dev/null && echo "frontend ok"
curl -fsS http://localhost:8000/healthz > /dev/null && echo "backend ok"
```

## Deployment topologies

The frontend image talks to backend via whatever URL you put in `VITE_HOST_API`. There is no in-container proxy — the browser calls the backend URL directly. Pick the shape:

### A. Split-domain

```
TLS proxy (Caddy/nginx)
   ├── app.example.com → frontend  (port 3000)
   └── api.example.com → backend   (port 8000)
```

Set `FRONTEND_URL=https://app.example.com` and `VITE_HOST_API=https://api.example.com`. Make sure backend's `CORS_ALLOWED_ORIGINS` includes `https://app.example.com`.

### B. Single-origin via reverse proxy route-split

```
TLS proxy (Caddy/nginx)
   app.example.com
     ├── /api/* → backend  (port 8000)
     └── /*    → frontend  (port 3000)
```

Set `VITE_HOST_API=/api` and let the proxy do the routing. Backend doesn't need CORS for cross-origin since SPA calls same origin.

Official Kubernetes manifests and Helm charts are coming soon. Until then, this production overlay is the supported self-hosting path.

## Reverse proxy + TLS

### Caddy

```
app.example.com {
    reverse_proxy localhost:3000
}
```

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name app.example.com;
    ssl_certificate     /etc/letsencrypt/live/app.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/app.example.com/privkey.pem;
    client_max_body_size 1G;
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Backups

### Postgres

```bash
# nightly cron
0 2 * * * docker compose exec -T postgres \
  pg_dump -U futureagi futureagi | gzip > /backups/pg-$(date +\%F).sql.gz
```

Restore:

```bash
gunzip < pg-2026-05-07.sql.gz | docker compose exec -T postgres psql -U futureagi futureagi
```

### ClickHouse

Holds traces, evals, analytics — also irreplaceable. Use `clickhouse-backup` (recommended) or a per-table dump:

```bash
# Per-table dump (simple, blocks reads briefly)
docker compose exec -T clickhouse clickhouse-client \
  --query "BACKUP DATABASE default TO File('/var/lib/clickhouse/backups/$(date +%F)')"
```

For incremental backups to S3, see [`clickhouse-backup`](https://github.com/Altinity/clickhouse-backup).

### MinIO

For internal MinIO, configure `mc mirror` to an off-host bucket, or replace the bundled `minio` service with managed S3 (set `STORAGE_BACKEND=s3` and supply AWS creds).

## Upgrades

```bash
# bump the relevant version variable(s) in deploy/.env.production
# (FUTURE_AGI_VERSION / FRONTEND_VERSION / FI_COLLECTOR_VERSION / AGENTCC_GATEWAY_VERSION /
#  SERVING_VERSION / CODE_EXECUTOR_VERSION / SIMULATION_RUNNER_VERSION)
docker compose --env-file deploy/.env.production \
  -f docker-compose.yml -f deploy/docker-compose.production.yml pull
docker compose --env-file deploy/.env.production \
  -f docker-compose.yml -f deploy/docker-compose.production.yml up -d --no-build --wait --wait-timeout 1200
```

Only use a separately rehearsed, compatible predecessor for rollback; restore its
exact image/configuration receipt after approval. Changing tags alone does not
undo schema/mirror changes or establish a healthy recovery. Preserve old data,
topics, volumes and obsolete workloads until their explicit retirement is approved.

## Resource sizing

| Service | RAM | CPU |
|---|---|---|
| backend | 1–2 GB | 1–2 cores |
| worker | 1 GB | 1 core |
| agentcc-gateway | 256 MB | 0.5 core |
| serving | 512 MB | 0.5 core |
| code-executor | 1 GB (cap) | 2 cores (cap) |
| postgres | 1–2 GB | 1 core |
| clickhouse | 2–4 GB | 2 cores |
| redis | 256 MB | 0.5 core |
| minio | 512 MB | 0.5 core |
| temporal | 512 MB | 0.5 core |
| **total** | **~10 GB** | **~10 cores** |

## Pre-flight checklist

- [ ] `SECRET_KEY`, `AGENTCC_INTERNAL_API_KEY`, `AGENTCC_ADMIN_TOKEN` are 32+ random bytes
- [ ] `PG_PASSWORD`, `MINIO_ROOT_PASSWORD`, `RABBITMQ_PASSWORD` set to non-default values
- [ ] Both catalog passwords match the provisioned identities; retained credentials were not rotated
- [ ] Check-only initialization prerequisite independently verified; no implicit production migrations or mirror repair
- [ ] Backend/workers/frontend and collector/consumer/backfill have matching source, registry digest and architecture receipts; `FI_COLLECTOR_VERSION` is explicit (not `local`/`latest`); remaining image versions are pinned
- [ ] `FRONTEND_URL` matches the public URL behind your reverse proxy
- [ ] `VITE_HOST_API` matches the public backend URL (or `/api` if route-split at the proxy)
- [ ] Backend CORS allows the frontend origin (split-domain only)
- [ ] Reverse proxy terminates TLS; frontend container is not exposed publicly on port 3000
- [ ] Postgres, ClickHouse, MinIO data volumes are on persistent storage
- [ ] Backup crons (Postgres + ClickHouse) scheduled and tested with restore dry-run
- [ ] Docker daemon and host OS get security patches on a known cadence
