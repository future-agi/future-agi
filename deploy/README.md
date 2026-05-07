# Production deployment

This directory holds the production overlay for self-hosted Future AGI. The base `docker-compose.yml` at the repo root is geared for local evaluation with safe defaults; this overlay re-binds required secrets with `${VAR:?error}` guards so compose refuses to boot on dev fallbacks.

## Quickstart

The interactive script generates secrets, prompts for the few things only you know (frontend URL, backend URL), pulls images, and boots the stack:

```bash
./deploy/setup.sh
```

Manual flow (equivalent to what `setup.sh` does):

```bash
cp deploy/.env.production.example deploy/.env.production
# fill in the REQUIRED values
docker compose --env-file deploy/.env.production \
  -f docker-compose.yml -f deploy/docker-compose.production.yml pull
docker compose --env-file deploy/.env.production \
  -f docker-compose.yml -f deploy/docker-compose.production.yml up -d
```

If any required value is empty, compose exits with `must be set for production` and names the missing var.

## Prerequisites

- Docker Engine 24.0+ and Docker Compose v2.24+
- A reverse proxy that terminates TLS in front of the frontend (Caddy / nginx / Traefik / ALB)
- (Optional) Managed Postgres and S3-compatible object store if you don't want the bundled `postgres` / `minio` containers
- 16 GB RAM minimum on the host (8 GB is OK for smoke tests; ClickHouse and the worker each hold ~1 GB)

## 1. Generate secrets

```bash
SECRET_KEY=$(openssl rand -hex 32)
AGENTCC_INTERNAL_API_KEY=$(openssl rand -hex 32)
AGENTCC_ADMIN_TOKEN=$(openssl rand -hex 32)
PG_PASSWORD=$(openssl rand -hex 16)
MINIO_ROOT_PASSWORD=$(openssl rand -hex 16)
```

Paste each into `deploy/.env.production`.

## 2. Pin a release

Set `FUTURE_AGI_VERSION` in `.env.production` to a specific tag (e.g. `v1.22.41`). Mutable tags (`v1.22`, `latest`) are discouraged in production — they shift under you on the next release.

## 3. Boot

```bash
docker compose --env-file deploy/.env.production \
  -f docker-compose.yml -f deploy/docker-compose.production.yml \
  pull
docker compose --env-file deploy/.env.production \
  -f docker-compose.yml -f deploy/docker-compose.production.yml \
  up -d
```

Verify:

```bash
docker compose ps
curl -fsS http://localhost:3000/ > /dev/null && echo "frontend ok"
curl -fsS http://localhost:8000/healthz > /dev/null && echo "backend ok"
```

## Deployment topologies

The frontend image talks to backend via whatever URL you put in `VITE_HOST_API`. There is no in-container proxy — the browser calls the backend URL directly. Pick the shape:

### A. Single-host docker compose, split-domain

```
TLS proxy (Caddy/nginx)
   ├── app.example.com → frontend  (port 3000)
   └── api.example.com → backend   (port 8000)
```

Set `FRONTEND_URL=https://app.example.com` and `VITE_HOST_API=https://api.example.com`. Make sure backend's `CORS_ALLOWED_ORIGINS` includes `https://app.example.com`.

### B. Single-host docker compose, single-origin via reverse proxy route-split

```
TLS proxy (Caddy/nginx)
   app.example.com
     ├── /api/* → backend  (port 8000)
     └── /*    → frontend  (port 3000)
```

Set `VITE_HOST_API=/api` and let the proxy do the routing. Backend doesn't need CORS for cross-origin since SPA calls same origin.

### C. Kubernetes, split-domain

Frontend at `app.example.com`, backend at `api.example.com`. Set `VITE_HOST_API=https://api.example.com` on the frontend Pod env. Configure backend CORS for `app.example.com`.

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
spec:
  rules:
    - host: app.example.com
      http:
        paths:
          - { path: /, pathType: Prefix, backend: { service: { name: frontend, port: { number: 80 } } } }
    - host: api.example.com
      http:
        paths:
          - { path: /, pathType: Prefix, backend: { service: { name: backend, port: { number: 80 } } } }
```

### D. Kubernetes, Ingress route-split (single host)

```yaml
spec:
  rules:
    - host: app.example.com
      http:
        paths:
          - { path: /api, pathType: Prefix, backend: { service: { name: backend, port: { number: 80 } } } }
          - { path: /,    pathType: Prefix, backend: { service: { name: frontend, port: { number: 80 } } } }
```

Set `VITE_HOST_API=/api`. Same-origin, no CORS work.

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
# bump FUTURE_AGI_VERSION in deploy/.env.production
docker compose --env-file deploy/.env.production \
  -f docker-compose.yml -f deploy/docker-compose.production.yml pull
docker compose --env-file deploy/.env.production \
  -f docker-compose.yml -f deploy/docker-compose.production.yml up -d
```

Roll back by setting `FUTURE_AGI_VERSION` to the previous tag and re-running the same two commands.

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
| **light total** | **~10 GB** | **~10 cores** |
| `full` adds PeerDB stack | +4 GB | +4 cores |

## Pre-flight checklist

- [ ] `SECRET_KEY`, `AGENTCC_INTERNAL_API_KEY`, `AGENTCC_ADMIN_TOKEN` are 32+ random bytes
- [ ] `PG_PASSWORD`, `MINIO_ROOT_PASSWORD`, `RABBITMQ_PASSWORD` set to non-default values
- [ ] `FUTURE_AGI_VERSION` pinned to an immutable `vX.Y.Z` tag (not `latest` / `vX.Y`)
- [ ] `FRONTEND_URL` matches the public URL behind your reverse proxy
- [ ] `VITE_HOST_API` matches the public backend URL (or `/api` if route-split at the proxy)
- [ ] Backend CORS allows the frontend origin (split-domain only)
- [ ] Reverse proxy terminates TLS; frontend container is not exposed publicly on port 3000
- [ ] Postgres, ClickHouse, MinIO data volumes are on persistent storage
- [ ] Backup crons (Postgres + ClickHouse) scheduled and tested with restore dry-run
- [ ] Docker daemon and host OS get security patches on a known cadence
