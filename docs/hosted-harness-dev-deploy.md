# Deploying the hosted harness on dev

Runbook for `future-agi-dev-gcp` (`https://dev.api.futureagi.com`). The harness stack is
the normal compose stack plus a pinned Daytona snapshot; a "deploy" is a pull, a pin,
and a recreate of two containers.

## What lives where

| Thing | Location on dev |
| --- | --- |
| Platform checkout | `/home/ubuntu/future-agi` (branch `feat/hosted-bundle-v2-production`) |
| ALK checkout (guest source for snapshots) | `/home/ubuntu/agent-learning-kit` (same branch name) |
| Daytona pins (snapshot, budgets, API key) | `/home/ubuntu/.env.daytona` — **not** read by compose; source it before `docker compose` |
| Provider keys, simulator config | `/home/ubuntu/future-agi/.env` (compose `env_file`) and `/home/ubuntu/future-agi/futureagi/.env` (host runs). Keep both in sync. |
| Containers that matter | `futureagi-backend-1`, `futureagi-worker-simulation-runner-1` (source is volume-mounted at `/app/backend`) |

Snapshot naming: `alk-hosted-bundle-v2-production-<date>-r<N>`. One snapshot per ALK
runtime release; every job gets a fresh sandbox from it.

## Deploy

```bash
ssh future-agi-dev-gcp

# 1. Pull both repos (fast-forward only; keep the local S3-creds compose tweak)
cd /home/ubuntu/future-agi
git stash push -m "dev-compose-s3-$(date +%s)" -- docker-compose.yml
git pull --ff-only origin feat/hosted-bundle-v2-production
git stash pop
cd /home/ubuntu/agent-learning-kit && git pull --ff-only origin feat/hosted-bundle-v2-production

# 2. Pin the snapshot (must already be `active` on Daytona — see "Snapshots")
sed -i 's|^ALK_DAYTONA_SNAPSHOT=.*|ALK_DAYTONA_SNAPSHOT=alk-hosted-bundle-v2-production-20260908-r44|' /home/ubuntu/.env.daytona

# 3. Rotate a key (both env files)
for f in /home/ubuntu/future-agi/.env /home/ubuntu/future-agi/futureagi/.env; do
  sed -i 's|^DEEPGRAM_API_KEY=.*|DEEPGRAM_API_KEY=<new key>|' "$f"
done

# 4. Recreate backend + worker with the pins in the environment
cd /home/ubuntu/future-agi
set -a; . /home/ubuntu/.env.daytona; set +a
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --no-deps --force-recreate \
  backend worker-simulation-runner

# 5. Re-install the Daytona SDK (the dev image predates it; a recreate drops pip installs)
for c in futureagi-backend-1 futureagi-worker-simulation-runner-1; do
  docker exec $c pip install -q --no-input daytona==0.207.0 httpx-ws==0.7.2 'urllib3>=2.1,<3'
done

# 6. Migrate if the pull brought migrations
docker exec futureagi-backend-1 sh -c 'cd /app/backend && python manage.py migrate --no-input'
```

## Verify

```bash
# pins reached the containers
for c in futureagi-backend-1 futureagi-worker-simulation-runner-1; do
  docker exec $c sh -c 'echo $ALK_DAYTONA_SNAPSHOT; echo $DEEPGRAM_API_KEY | cut -c1-6'
done
docker exec futureagi-backend-1 sh -c 'cd /app/backend && python manage.py showmigrations --plan | grep -c "^\[ \]"'   # expect 0
curl -s -o /dev/null -w '%{http_code}\n' https://dev.api.futureagi.com/api/deployment-info/    # expect 200

# worker registered the gateway workflow (init takes 2-4 min: torch + NLTK)
docker logs futureagi-worker-simulation-runner-1 2>&1 | grep -c HostedHarnessGatewayWorkflow   # expect >= 1
```

Then run a small job from the UI (`/dashboard/harness`, 2 scenarios). Stage flow should be
`queued -> provisioning -> acquiring_source -> understanding_agent -> ... -> completed`.
Inspect a job from the backend container:

```bash
docker exec -i futureagi-backend-1 python - <<'PY'
import os, logging; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tfc.settings.settings')
import django; django.setup(); logging.disable(logging.CRITICAL)
from simulate.models import HostedHarnessJob
for j in HostedHarnessJob.no_workspace_objects.order_by('-created_at')[:5]:
    a = j.attempts.order_by('-attempt_number').first()
    print(str(j.id)[:8], j.state, j.current_stage, a and a.snapshot_name, (a and a.terminal_failure or {}).get('code'))
PY
```

## Snapshots

Build from the ALK checkout (needs `DAYTONA_API_KEY` from `/home/ubuntu/.env.daytona`):

```bash
cd /home/ubuntu/agent-learning-kit && git pull --ff-only
# bump the cache-buster so Daytona rebuilds from the new source
sed -i "s|^ARG ALK_HOSTED_SOURCE_REVISION=.*|ARG ALK_HOSTED_SOURCE_REVISION=$(date +%Y%m%d)-bundle-v2-$(git rev-parse --short HEAD)-rNN|" Dockerfile.hosted
# /home/ubuntu/build_snap2.py: edit NAME= at the top, then run it inside the worker
# (it reads /opt/alk-source/Dockerfile.hosted, the ALK checkout mounted into the worker)
docker exec -i futureagi-worker-simulation-runner-1 python - < /home/ubuntu/build_snap2.py
```

Wait until the snapshot is `active` before pinning; sandboxes on a building snapshot fail at launch.
Snapshots are pruned aggressively on the Daytona org (~15 kept) — if a pinned snapshot disappears,
every job fails at launch with Daytona not-found. Do not delete the pinned one.

## Mirror to the deployment repo

Every deploy-relevant change on dev (snapshot pin, `ALK_HOSTED_*` budgets, `SIMULATOR_*`,
provider keys) goes to `future-agi/deployment` branch `feat/alk-hosted-daytona-eu-prod` in the
same change, in **both** `us/gcp/deployment/values.yaml` and `eu/gcp/deployment/values.yaml`
(`backend.secret.data` and `core_backend_worker_l.secret.data`; the sim-runner pod
`envFrom`s the worker-L secret). Only `HARNESS_PUBLIC_BASE_URL` may differ between regions.
Never leave an empty-string value in `secret.data` (`b64enc ""` renders a null the API server rejects).

```bash
helm template futureagi us/gcp/deployment -f us/gcp/deployment/values.yaml | grep -A2 ALK_DAYTONA_SNAPSHOT
```

## Gotchas

- `--force-recreate` of `backend` also stops containers that depend on it (other people's
  `pr*-*` test containers). Warn in the channel first.
- The worker prints nothing useful for several minutes after start; `0` registrations
  right after a recreate is normal. Use `py-spy dump` on the worker PID if it exceeds ~5 min.
- Runs authored before 2026-09-07 (old file-allowlist archives) cannot be extended with
  "add scenarios" — they fail with `No world at /work/authoring`. Re-run them once.
- Preflight rejects `connector: auto` jobs without a full credential family
  (LiveKit URL+key+secret, or `VAPI_API_KEY`, or `RETELL_API_KEY`).
