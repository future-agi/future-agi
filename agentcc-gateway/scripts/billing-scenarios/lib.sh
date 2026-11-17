# shellcheck shell=bash
# Billing scenarios: money through the real gateway binary, the mock upstream,
# one billing Redis and Django's managed-credit ledger and settlement drain
# (plan section 5, D3; BPC#30). Source it from a billing scenario:
#
#   source "$(dirname "$0")/lib.sh"   # also sources scenarios/lib.sh
#   scn_up                            # the gateway and the mock (scenarios/lib.sh)
#   bs_django_up                      # Django's database, created and migrated
#   bs_org "my scenario"              # a Django org in BS_ORG; also the gateway org id
#   bs_grant "$BS_ORG" 10             # E1's grant, pushed to the gateway on commit
#   bs_drain_quiesce "$BS_ORG"        # D3's drain until nothing is left to settle
#
# Django runs through a manage.py wrapper script, the gate convention:
#   GATE_DJANGO_MANAGE   the gate's wrapper; unset, $SCN_HOME/bs-manage.sh, which
#                        bs_django_up writes from the lane's $SCN_HOME/lane-test.sh
#                        (same database settings, manage.py instead of pytest).
# Django calls the scenario gateway's admin API (AGENTCC_GATEWAY_INTERNAL_URL and
# AGENTCC_ADMIN_TOKEN, which the wrapper does not set) and the drain reads the
# scenario's Redis DB (SCN_REDIS, SCN_REDIS_DB), so the gateway and Django share
# one billing Redis whatever REDIS_URL the wrapper exports.
#
# Every bs_* function that runs Django writes its output to $SCN_DIR/django.log
# and its result, as JSON, to $SCN_DIR/bs-<name>.json.

BS_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SCN_REDIS=${SCN_REDIS:-127.0.0.1:16379} # managed billing fails closed without Redis
# shellcheck source=../scenarios/lib.sh
source "$BS_ROOT/../scenarios/lib.sh"

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

# bs_grant <org> <usd>: grant usd through E1's ledger service, whose on-commit
# push sends the gateway the granted total; fails unless the gateway acked it.
bs_grant() {
  bs_django_py grant "
from decimal import Decimal
from uuid import uuid4
from accounts.models.organization import Organization
from ee.usage.services import managed_credits
org = Organization.objects.get(pk=BS_ARGS[0])
micros = int(Decimal(BS_ARGS[1]) * 1000000)
managed_credits.grant(org, micros, idempotency_key=str(uuid4()), actor=None, note='billing scenario')
account = managed_credits.get_account(org)
bs_out({'granted_total_micros': account.granted_total_micros, 'version': account.version,
        'gateway_acked_version': account.gateway_acked_version})" "$1" "$2"
  local version acked
  version=$(jq -r .version "$SCN_DIR/bs-grant.json")
  acked=$(jq -r .gateway_acked_version "$SCN_DIR/bs-grant.json")
  [ "$version" = "$acked" ] ||
    scn_fail "bs_grant $1" "the gateway did not ack version $version (acked $acked): $(tail -20 "$SCN_DIR/django.log")"
}

# bs_drain [name]: run D3's drain once against the scenario's Redis; the run's
# counts are in $SCN_DIR/bs-<name>.json (default drain).
bs_drain() {
  bs_django_py "${1:-drain}" "
from ee.usage.services.managed_settlement import drain_managed_settlements
bs_out(drain_managed_settlements(bs_redis()))"
}

# bs_drain_quiesce <org> [max runs]: drain until a run handles no entry and the
# org's outbox has nothing pending; the runs' counts are in bs-quiesce.json.
bs_drain_quiesce() {
  bs_django_py quiesce "
from ee.usage.services import managed_settlement as ms
client = bs_redis()
runs = []
for _ in range(int(BS_ARGS[1])):
    counts = ms.drain_managed_settlements(client)
    runs.append(counts)
    handled = sum(counts[k] for k in ('settled', 'redelivered', 'voids', 'dead'))
    pending = client.xpending(ms.outbox_key(BS_ARGS[0]), ms.GROUP)['pending']
    if handled == 0 and pending == 0:
        break
bs_out({'runs': runs, 'quiet': handled == 0 and pending == 0})" "$1" "${2:-10}"
  [ "$(jq -r .quiet "$SCN_DIR/bs-quiesce.json")" = true ] ||
    scn_fail "bs_drain_quiesce $1" "the drain never quiesced: $(cat "$SCN_DIR/bs-quiesce.json")"
}

# bs_ledger <org> [name]: the org's ledger as JSON in $SCN_DIR/bs-<name>.json
# (default ledger): spend entries by request id (billed micros, upstream
# micros, usage_estimated), their count and sum, and the account's totals.
bs_ledger() {
  bs_django_py "${2:-ledger}" "
from accounts.models.organization import Organization
from ee.usage.models.managed_credits import ManagedCreditEntry
from ee.usage.services import managed_credits
org = Organization.objects.get(pk=BS_ARGS[0])
spends = ManagedCreditEntry.objects.filter(organization=org, kind='spend')
by_request = {e.request_id: {'billed_micros': -e.amount_micros, 'upstream_micros': e.upstream_cost_micros,
                             'usage_estimated': e.usage_estimated, 'attempt_count': e.attempt_count}
              for e in spends}
bs_out({'spend_count': spends.count(), 'distinct_requests': len(by_request),
        'spend_micros': sum(v['billed_micros'] for v in by_request.values()),
        'upstream_micros': sum(v['upstream_micros'] or 0 for v in by_request.values()),
        'by_request': by_request, **managed_credits.balance(org)})" "$1"
}

# --- the gateway ----------------------------------------------------------------

# bs_credits_field <org> <field>: a field of the gateway's GET /-/orgs/{org}/credits.
bs_credits_field() {
  local code
  code=$(scn_admin GET "/-/orgs/$1/credits")
  [ "$code" = 200 ] || scn_fail "bs_credits_field $1" "GET credits returned HTTP $code: $(cat "$SCN_DIR/last.body")"
  jq -r ".$2" <"$SCN_DIR/last.body"
}

# bs_load <plan.json> <out.jsonl> [workers]: send the plan's calls to the gateway
# with the Bearer $SCN_KEY, workers at a time (default 8), appending one line per
# step to out.jsonl. The plan is a list of calls, each {"name", "steps": [step]};
# a call's steps run in order (a client retry follows its failure). A step is
# {"body", "headers"?, "stream"?, "abort_after_s"?, "arm"?}: "arm" is a mock
# behaviour ({"behavior", "model"?}) armed just before the step, so plans that
# arm run with one worker; the mock is disarmed at the end. Each line has name,
# step, status, request_id, cost (micros, from x-agentcc-cost), body_cost
# (micros, from the body's usage.cost, or the last stream chunk's), replay
# (x-agentcc-idempotent-replay) and aborted (the client hung up at
# abort_after_s, like curl --max-time).
bs_load() {
  python3 -I - "$SCN_GW" "$SCN_KEY" "$1" "$2" "${3:-8}" "$SCN_MOCK" <<'PY'
import concurrent.futures, http.client, json, select, socket, sys, time, urllib.parse, urllib.request
from decimal import Decimal
gw, key, plan_path, out_path, workers, mock = sys.argv[1:7]
url = urllib.parse.urlsplit(gw)

def mock_behavior(method, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    urllib.request.urlopen(urllib.request.Request(mock + "/__behavior", data=data, method=method)).read()

def micros(usd):
    if usd is None or usd == "":
        return None
    return int(Decimal(str(usd)) * 1000000)

def usage_cost(payload):
    usage = payload.get("usage") if isinstance(payload, dict) else None
    return micros(usage.get("cost")) if isinstance(usage, dict) and "cost" in usage else None

def dechunk(raw):
    out = b""
    while raw:
        size_line, _, raw = raw.partition(b"\r\n")
        size = int(size_line.split(b";")[0], 16)
        if size == 0:
            break
        out, raw = out + raw[:size], raw[size + 2:]
    return out

def post_until(body, headers, deadline):
    """POST over a raw socket, reading until the deadline like curl --max-time:
    (status, headers, body), the body None when the deadline passed first."""
    payload = json.dumps(body).encode()
    head = ["POST /v1/chat/completions HTTP/1.1", "Host: " + url.netloc,
            "Content-Length: %d" % len(payload), "Connection: close"]
    head += ["%s: %s" % kv for kv in headers.items()]
    sock = socket.create_connection((url.hostname, url.port), timeout=10)
    data, finished = b"", False
    try:
        sock.sendall(("\r\n".join(head) + "\r\n\r\n").encode() + payload)
        while time.monotonic() < deadline:
            if not select.select([sock], [], [], max(0.0, deadline - time.monotonic()))[0]:
                continue
            part = sock.recv(65536)
            if not part:
                finished = True
                break
            data += part
    finally:
        sock.close()  # before the deadline's end of stream: the client hangs up
    head_raw, _, rest = data.partition(b"\r\n\r\n")
    lines = head_raw.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1]) if lines[0] else None
    found = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}
    if not finished:
        return status, found, None
    chunked = found.get("transfer-encoding", "").lower() == "chunked"
    return status, found, dechunk(rest) if chunked else rest

def post(body, headers):
    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=60)
    try:
        conn.request("POST", "/v1/chat/completions", json.dumps(body), headers)
        resp = conn.getresponse()
        found = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, found, resp.read()
    finally:
        conn.close()

def step(name, i, s):
    if "arm" in s:
        mock_behavior("POST", {"behavior": s["arm"]["behavior"], "model": s["arm"].get("model", "")})
    body = dict(s["body"], stream=True) if s.get("stream") else s["body"]
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", **s.get("headers", {})}
    if "abort_after_s" in s:
        status, found, raw = post_until(body, headers, time.monotonic() + s["abort_after_s"])
    else:
        status, found, raw = post(body, headers)
    row = {"name": name, "step": i, "status": status, "request_id": found.get("x-agentcc-request-id"),
           "cost": micros(found.get("x-agentcc-cost")), "replay": found.get("x-agentcc-idempotent-replay"),
           "aborted": raw is None, "body_cost": None}
    if raw is None:
        return row  # the client gave up mid-stream and hung up
    text = raw.decode()
    if s.get("stream"):
        chunks = [json.loads(l[6:]) for l in text.splitlines() if l.startswith("data: {")]
        costs = [usage_cost(c) for c in chunks if usage_cost(c) is not None]
        row["body_cost"] = costs[-1] if costs else None
    else:
        try:
            row["body_cost"] = usage_cost(json.loads(text))
        except ValueError:
            pass
    return row

def call(c):
    return [step(c["name"], i, s) for i, s in enumerate(c["steps"])]

plan = json.load(open(plan_path))
try:
    with concurrent.futures.ThreadPoolExecutor(int(workers)) as pool, open(out_path, "a") as out:
        for rows in pool.map(call, plan):
            for row in rows:
                out.write(json.dumps(row) + "\n")
finally:
    if any("arm" in s for c in plan for s in c["steps"]):
        mock_behavior("DELETE")
PY
}
