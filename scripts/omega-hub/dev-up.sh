#!/bin/sh
# The Hub dev stack (SPEC §14.1 acceptance 4, §16): the integrated Django (tfc.settings.settings) on
# 127.0.0.1:8740, Postgres and Redis from the local test stack, MinIO (test stack, :39005) in a lane
# bucket, the seed (maintainers A and B, the Official publisher, A's Hub token), and the checker
# worker driving the runner image under Colima. Loopback only. Stop it with dev-down.sh.
#
#   scripts/omega-hub/dev-up.sh            # needs futureagi/.venv and a built runner image
#   OMEGA_HUB_RUNNER_IMAGE=omega-hub-runner:881b05b scripts/omega-hub/dev-up.sh
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
FA="$ROOT/futureagi"
CHECKER="$ROOT/services/omega-hub-checker"
PY="$FA/.venv/bin/python"
STATE="${OMEGA_HUB_LANE_STATE:-${TMPDIR:-/tmp}/omega-hub-lane}"
PORT="${OMEGA_HUB_PORT:-8740}"
. "$ROOT/scripts/omega-hub/lane-env.sh"
[ -x "$PY" ] || { echo "no $PY: run 'uv sync --frozen --all-extras' in futureagi/ first" >&2; exit 2; }
mkdir -p "$STATE"
if [ -f "$STATE/django.pid" ] && kill -0 "$(cat "$STATE/django.pid")" 2>/dev/null; then
  echo "already up (pid $(cat "$STATE/django.pid")); run dev-down.sh first" >&2; exit 2
fi

export PG_DB="${OMEGA_HUB_DEV_DB:-omega_hub_dev}"
export OMEGA_HUB_BUCKET="${OMEGA_HUB_BUCKET:-omega-hub-lane}"
export OMEGA_HUB_PUBLIC_API_URL="http://127.0.0.1:$PORT/omega-hub/v1"
export OMEGA_HUB_ALLOW_DEV_APPROVE=1
TOKEN_FILE="$STATE/checker-token"
[ -s "$TOKEN_FILE" ] || (umask 077; "$PY" -c 'import secrets; print(secrets.token_urlsafe(32))' > "$TOKEN_FILE")
chmod 600 "$TOKEN_FILE"
export OMEGA_HUB_CHECKER_TOKEN="$(cat "$TOKEN_FILE")"

# Storage: the test stack's MinIO when it answers, else a folder (DEBUG/test settings only).
if "$PY" - <<'PYEOF' 2>/dev/null
from minio import Minio
Minio("localhost:39005", access_key="minioadmin", secret_key="minioadmin", secure=False).list_buckets()
PYEOF
then
  export OMEGA_HUB_STORAGE=object STORAGE_BACKEND=minio S3_ENDPOINT_URL=http://localhost:39005 S3_SECURE=false \
    S3_ACCESS_KEY=minioadmin S3_SECRET_KEY=minioadmin
  echo "storage: MinIO localhost:39005, bucket $OMEGA_HUB_BUCKET"
else
  export OMEGA_HUB_STORAGE=filesystem OMEGA_HUB_STORAGE_DIR="$STATE/store"
  echo "storage: $OMEGA_HUB_STORAGE_DIR (MinIO not reachable)"
fi

"$PY" - <<'PYEOF'
import os, psycopg
conn = psycopg.connect(host="localhost", port=os.environ["PG_PORT"], user="test_user", password="test_password",
                       dbname="test_tfc", autocommit=True)
name = os.environ["PG_DB"]
if not conn.execute("select 1 from pg_database where datname = %s", (name,)).fetchone():
    conn.execute(f'create database "{name}"')
    print(f"created database {name}")
PYEOF
cd "$FA"
echo "migrating $PG_DB (the first run takes a few minutes)"
"$PY" manage.py migrate --settings="$OMEGA_HUB_SETTINGS" > "$STATE/migrate.log" 2>&1 || { tail -20 "$STATE/migrate.log"; exit 1; }
# manage.py runs only allowlisted commands at startup; shell -c is allowed in local mode (hub-manage.sh).
OMEGA_HUB_CMD=$("$PY" -c 'import json,sys; print(json.dumps(sys.argv[1:]))' omega_hub_seed_dev \
  --password "${OMEGA_HUB_DEV_PASSWORD:-omega-hub-dev-password}" --token-file "$STATE/token-a") \
  "$PY" manage.py shell --settings="$OMEGA_HUB_SETTINGS" -c \
  "import json, os; from django.core.management import call_command; call_command(*json.loads(os.environ['OMEGA_HUB_CMD']))" \
  2>/dev/null > "$STATE/seed.json"
grep -v password "$STATE/seed.json"
# Django's startup logs share stdout with the command's JSON, and their key=value fields can hold "{":
# keep from the last line that starts with "{", which is where the seed's pretty-printed summary begins.
ORG_ID="$("$PY" -c 'import json,re,sys; t=open(sys.argv[1]).read(); print(json.loads(t[[m.start() for m in re.finditer(r"(?m)^[{]", t)][-1]:])["maintainerOrgId"])' "$STATE/seed.json")"
[ -n "$ORG_ID" ] || { echo "the seed did not report the maintainer organisation" >&2; exit 1; }
export OMEGA_HUB_MAINTAINER_ORG_ID="$ORG_ID"
env | grep -E '^(PG_|OMEGA_HUB_|STORAGE_BACKEND|S3_|CH|FI_|REDIS|NO_STARTUP|FUTURE_AGI_LICENSE_URL|DOCKER_CONTEXT)' \
  | grep -v -E 'CHECKER_TOKEN|S3_SECRET' | sed 's/^/export /' > "$STATE/env.sh"
nohup "$PY" manage.py runserver "127.0.0.1:$PORT" --noreload --settings="$OMEGA_HUB_SETTINGS" > "$STATE/django.log" 2>&1 &
echo $! > "$STATE/django.pid"
i=0
until curl -fsS "http://127.0.0.1:$PORT/omega-hub/v1/rules" >/dev/null 2>&1; do
  i=$((i + 1)); [ $i -lt 120 ] || { tail -30 "$STATE/django.log"; exit 1; }; sleep 1
done
echo "django: http://127.0.0.1:$PORT (pid $(cat "$STATE/django.pid"))"

IMAGE="${OMEGA_HUB_RUNNER_IMAGE:-}"
[ -n "$IMAGE" ] || { [ -f "$CHECKER/runner/.built" ] && IMAGE=$(cut -d' ' -f1 "$CHECKER/runner/.built"); } || true
if [ -n "$IMAGE" ] && docker image inspect "$IMAGE" >/dev/null 2>&1; then
  [ -d "$CHECKER/node_modules/typescript" ] || (cd "$CHECKER" && npm ci --no-audit --no-fund >/dev/null)
  OMEGA_HUB_URL="http://127.0.0.1:$PORT" OMEGA_HUB_RUNNER_IMAGE="$IMAGE" OMEGA_HUB_POLL_MS=2000 OMEGA_HUB_CHECKER_ID=lane-checker \
    nohup node "$CHECKER/src/main.mjs" > "$STATE/checker.log" 2>&1 &
  echo $! > "$STATE/checker.pid"
  echo "checker: runner $IMAGE (pid $(cat "$STATE/checker.pid"))"
else
  echo "checker: NOT started (no runner image; build one with services/omega-hub-checker/runner/build.sh)"
fi
echo "state: $STATE (token-a, seed.json, env.sh, *.log)"
