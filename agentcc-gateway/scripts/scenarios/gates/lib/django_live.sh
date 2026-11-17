# shellcheck shell=bash
# Shared setup of the gates that run Django live against the scenario gateway: CW2_ee_provisioning_live.sh,
# CW2c_ee_scoped_key_live.sh and W3b_isolation_live.sh. Source it from such a gate after setting SCN_LANE:
#
#   source "$(dirname "$0")/lib/django_live.sh"
#
# Stage 2 port. On the integration and connector lines these gates sourced
# lib/w3_common.sh, the money gates' setup, which sources billing-scenarios/lib.sh (Django's managed-credit
# ledger and settlement drain). Neither file is on the release line, whose billing is not that one, and none of
# these gates calls a money helper. This file carries only what they use, copied unchanged from e069e8b9d1:
# w3_common.sh's Redis check, scratch directories and w3_need_django, and billing-scenarios/lib.sh's Redis and
# Django helpers (bs_rcmd, bs_manage, bs_django_py, bs_django_up, bs_org).
#
# Like run_all.sh, it refuses to run without SCN_REDIS and a positive SCN_REDIS_DB. Scratch goes to
# $GATE_HOME/scn-live (GATE_HOME defaults to lanes/X6-gates), never /tmp. A check that runs Django needs
# GATE_DJANGO_MANAGE, a manage.py wrapper with the gate's database settings; Django pushes to the scenario
# gateway (AGENTCC_GATEWAY_INTERNAL_URL and AGENTCC_ADMIN_TOKEN, which the wrapper does not set).
if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "$(basename "$0"): SCN_REDIS and a non-zero SCN_REDIS_DB are required (got" \
    "SCN_REDIS='${SCN_REDIS:-}' SCN_REDIS_DB='${SCN_REDIS_DB:-}')" >&2
  exit 1
fi
DL_GATES=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
GATE_HOME=${GATE_HOME:-$HOME/.futureagi-local/gateway-program/lanes/X6-gates}
export SCN_LANE=${SCN_LANE:-x6-django-live}
export SCN_HOME=${SCN_HOME:-$GATE_HOME/scn-live}
mkdir -p "$SCN_HOME"
# shellcheck source=../../lib.sh
source "$DL_GATES/../lib.sh"

# w3_need_django: stop unless GATE_DJANGO_MANAGE names the gate's wrapper.
w3_need_django() {
  [ -n "${GATE_DJANGO_MANAGE:-}" ] && [ -f "$GATE_DJANGO_MANAGE" ] ||
    scn_fail django "GATE_DJANGO_MANAGE must name the gate's manage.py wrapper (got '${GATE_DJANGO_MANAGE:-}')"
}

# --- Redis ----------------------------------------------------------------------

# bs_rcmd <command> [args...]: one Redis command in the scenario's DB; prints
# the reply (an integer or a string; a list prints one element per line).
bs_rcmd() {
  python3 -I - "$SCN_REDIS" "${SCN_REDIS_DB:-0}" "$@" <<'PY'
import socket, sys
host, port = sys.argv[1].rsplit(":", 1)
sock = socket.create_connection((host, int(port)), timeout=5)
rd = sock.makefile("rb")
def reply():
    line = rd.readline()
    kind, rest = line[:1], line[1:-2]
    if kind == b"-":
        raise SystemExit("redis: " + rest.decode())
    if kind in (b"+", b":"):
        return rest.decode()
    if kind == b"$":
        n = int(rest)
        return "" if n < 0 else rd.read(n + 2)[:-2].decode()
    return [reply() for _ in range(int(rest))]
def cmd(*args):
    out = b"*%d\r\n" % len(args)
    for a in args:
        a = str(a).encode()
        out += b"$%d\r\n%s\r\n" % (len(a), a)
    sock.sendall(out)
    return reply()
cmd("SELECT", sys.argv[2])
result = cmd(*sys.argv[3:])
print("\n".join(map(str, result)) if isinstance(result, list) else result)
PY
}

# --- Django -----------------------------------------------------------------------

_bs_manage_script() { # the manage.py wrapper to use (written on first use)
  if [ -n "${GATE_DJANGO_MANAGE:-}" ]; then
    echo "$GATE_DJANGO_MANAGE"
    return
  fi
  local lane_test="$SCN_HOME/lane-test.sh" out="$SCN_HOME/bs-manage.sh"
  [ -f "$lane_test" ] ||
    scn_fail bs_django "GATE_DJANGO_MANAGE is unset and $lane_test does not exist (plan section 4A)"
  grep -q -- '-m pytest "\$@"' "$lane_test" ||
    scn_fail bs_django "$lane_test does not end in 'python -m pytest \"\$@\"'"
  sed 's#-m pytest "\$@"#manage.py "$@"#' "$lane_test" >"$out"
  echo "$out"
}

# bs_manage <manage.py args...>: run manage.py in the scenario's Django env, in
# the explicit local mode that allows migrate and shell (NO_STARTUP_DB_MUTATIONS
# false, as ./bin/dev manage runs; never in a hosted deployment).
bs_manage() {
  [ -n "${SCN_GW:-}" ] || scn_fail bs_django "scn_up must run first: Django pushes to the scenario gateway"
  env AGENTCC_GATEWAY_INTERNAL_URL="$SCN_GW" AGENTCC_ADMIN_TOKEN="$SCN_ADMIN_TOKEN" \
    BS_REDIS_URL="redis://$SCN_REDIS/${SCN_REDIS_DB:-0}" NO_STARTUP_DB_MUTATIONS=false \
    bash "$(_bs_manage_script)" "$@" >>"$SCN_DIR/django.log" 2>&1
}

# bs_django_py <name> <python code> [args...]: run code with Django set up. The
# code gets BS_ARGS (the extra args), bs_redis() (the scenario's Redis) and
# bs_out(obj), which writes $SCN_DIR/bs-<name>.json. A failure stops the script.
bs_django_py() {
  local name=$1 code=$2 out
  shift 2
  out="$SCN_DIR/bs-$name.json"
  rm -f "$out"
  BS_OUT="$out" BS_ARGS=$(python3 -I -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@") \
    bs_manage shell -c "
import json, os
import redis
BS_ARGS = json.loads(os.environ['BS_ARGS'])
def bs_redis():
    return redis.Redis.from_url(os.environ['BS_REDIS_URL'], decode_responses=True)
def bs_out(obj):
    with open(os.environ['BS_OUT'], 'w') as f:
        json.dump(obj, f, default=str)
$code" || scn_fail "bs_django_py $name" "Django failed: $(tail -20 "$SCN_DIR/django.log")"
  [ -s "$out" ] || scn_fail "bs_django_py $name" "no result written: $(tail -20 "$SCN_DIR/django.log")"
}

# bs_django_up: create the wrapper's database when it is missing, then migrate it.
bs_django_up() {
  bs_django_py db_create "
from django.db import connection
name = connection.settings_dict['NAME']
with connection._nodb_cursor() as cursor:
    cursor.execute('SELECT 1 FROM pg_database WHERE datname = %s', [name])
    created = cursor.fetchone() is None
    if created:
        cursor.execute('CREATE DATABASE ' + connection.ops.quote_name(name))
bs_out({'database': name, 'created': created})"
  echo "bs_django_up: database $(jq -r .database "$SCN_DIR/bs-db_create.json") (created: $(jq -r .created "$SCN_DIR/bs-db_create.json")); migrating"
  bs_manage migrate --noinput || scn_fail bs_django_up "migrate failed: $(tail -20 "$SCN_DIR/django.log")"
}

# bs_org <name>: create a Django org and set BS_ORG to its id (the gateway org
# id). Not for $(...): a failure must stop the script.
bs_org() {
  bs_django_py org "
from accounts.models.organization import Organization
bs_out({'org_id': str(Organization.objects.create(name=BS_ARGS[0]).pk)})" "$1"
  BS_ORG=$(jq -r .org_id "$SCN_DIR/bs-org.json")
  _scn_track_org "$BS_ORG"
}

