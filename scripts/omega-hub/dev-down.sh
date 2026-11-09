#!/bin/sh
# Stop what dev-up.sh started: the checker, Django, any ohc-* container, the lane's MinIO bucket,
# and (unless --keep-db) the lane's dev database. Shared test-stack containers are left running.
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PY="$ROOT/futureagi/.venv/bin/python"
STATE="${OMEGA_HUB_LANE_STATE:-${TMPDIR:-/tmp}/omega-hub-lane}"
. "$ROOT/scripts/omega-hub/lane-env.sh"
for name in checker django; do
  if [ -f "$STATE/$name.pid" ]; then
    pid=$(cat "$STATE/$name.pid")
    kill "$pid" 2>/dev/null && echo "stopped $name ($pid)"
    sleep 1; kill -9 "$pid" 2>/dev/null || true
    rm -f "$STATE/$name.pid"
  fi
done
ids=$(docker ps -aq --filter "name=ohc-" 2>/dev/null)
[ -z "$ids" ] || { docker rm -f $ids >/dev/null && echo "removed ohc-* containers"; }
# The bucket is versioned with object lock: teardown_bucket.py removes every version, then the bucket,
# and says so when it could not (it used to delete by name, which only added delete markers, and hid it).
bucket_rc=0
"$PY" "$ROOT/scripts/omega-hub/teardown_bucket.py" "${OMEGA_HUB_BUCKET:-omega-hub-lane}" || bucket_rc=$?
if [ "${1:-}" != "--keep-db" ]; then
  "$PY" - "${OMEGA_HUB_DEV_DB:-omega_hub_dev}" <<'PYEOF' 2>/dev/null || true
import os, sys, psycopg
conn = psycopg.connect(host="localhost", port=os.environ.get("PG_PORT", "15499"), user="test_user", password="test_password",
                       dbname="test_tfc", autocommit=True)
conn.execute(f'drop database if exists "{sys.argv[1]}" with (force)')
print(f"dropped database {sys.argv[1]}")
PYEOF
  rm -rf "$STATE/store"
fi
[ "$bucket_rc" -eq 0 ] || { echo "dev-down: the lane bucket was not removed (above)" >&2; exit 1; }
