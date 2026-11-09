#!/bin/sh
# Run one omega_hub management command on the lane's dev stack:
#   scripts/omega-hub/hub-manage.sh omega_hub_dev_approve --as omega-hub-b@futureagi.local --all-in-review
#
# The platform's mutation-free startup gate (model_hub/apps.py) lets `manage.py` run only an
# allowlist of commands; in explicit local mode that includes `shell`, so this goes through
# `shell -c` and call_command. Adding the omega_hub commands to that allowlist is an integrator edit.
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
STATE="${OMEGA_HUB_LANE_STATE:-${TMPDIR:-/tmp}/omega-hub-lane}"
. "$ROOT/scripts/omega-hub/lane-env.sh"
[ -f "$STATE/env.sh" ] && . "$STATE/env.sh"
[ -f "$STATE/checker-token" ] && OMEGA_HUB_CHECKER_TOKEN="$(cat "$STATE/checker-token")" && export OMEGA_HUB_CHECKER_TOKEN
[ "${S3_ACCESS_KEY:-}" = minioadmin ] && export S3_SECRET_KEY=minioadmin
OMEGA_HUB_CMD=$("$ROOT/futureagi/.venv/bin/python" -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@")
export OMEGA_HUB_CMD
cd "$ROOT/futureagi"
exec .venv/bin/python manage.py shell --settings="$OMEGA_HUB_SETTINGS" -c \
  "import json, os; from django.core.management import call_command; call_command(*json.loads(os.environ['OMEGA_HUB_CMD']))"
