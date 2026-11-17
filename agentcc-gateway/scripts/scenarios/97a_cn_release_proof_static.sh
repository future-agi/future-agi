#!/usr/bin/env bash
# The release proof of the static-credential path. Two orgs, each
# with an Owner and a Member, on the stack: the real gateway binary built from this tree; Django's dev
# server as its control plane (keys and org configs synced every 2 s, tool_call logs ingested through
# its webhook), on Postgres and Redis; agentcc-mcpmock upstreams. Every person acts through the
# console's own routes with a session token, and every key through /mcp. It proves:
#
#   org isolation      each org's keys list and call only its own org-saved server and catalogue
#                      connector, with its own vault credential (the upstreams' SHA-256 fingerprints);
#                      sessions, virtual-server ids, credential ids and tool-call rows of one org are 404
#                      to the other; a foreign X-Organization-Id acts on the caller's own org; a Member
#                      may read but not write.
#   SSRF               Test connection refuses cloud metadata, private and unlisted loopback addresses
#                      (egress_refused); a catalogue add whose host resolves to metadata, or is an IP
#                      literal, is refused and writes nothing; an org-saved metadata server never connects.
#   one record         every tools/call the gateway answered has exactly one tool_call row, in
#                      its own org, found by the call's x-agentcc-request-id; the admin playground's call
#                      is one admin row.
#   write tools        a catalogue add sets each listed tool's posture from its annotations (read-only:
#                      allow; write, destructive or unannotated: allowed after an approval);
#                      those are listed, a call is refused in band (approval_required) and the upstream
#                      never sees it. An org with no virtual server gets access.default deny.
#   kill switch        an org's mcp.enabled false stops its servers (the other org runs on), and the
#                      operator's mcp.enabled false (a restart) takes /mcp and Django's MCP reads away
#                      while chat still runs; no upstream is reached, no row is written. The console's
#                      MCP status says gateway_mcp_disabled (the Integrations page shows the off state
#                      from it), the catalogue list still answers, and an add by an org that has added
#                      nothing (org C) is 404 gateway_mcp_disabled and writes nothing.
#   no credential      no upstream token, catalogue key, webhook secret, gateway key or session token is in
#                      any log, any response (keys only in their creation answer, which is not kept), any
#                      GET, or any table of the database (the vault holds ciphertext).
#
# It writes a receipt JSON naming the commit (CN30A_RECEIPT, default $SCN_HOME/cn30a-receipt.json), on a
# red too, with the failed assertion.
#
# Deviations, also in the receipt: the catalogue is a fixture app (fixtures/CN30a/catalog) whose provider
# is agentcc-mcpmock behind a TLS pipe on this machine's DNS name, since the real catalogue's providers are
# vendor hosts this run has no accounts on. agentcc-catalog builds the Django bundle from it, the gateway
# loads it as connectors.catalog_dir, and Django's dev server reads the bundle (connector_catalog.BUNDLE_PATH
# set before runserver): the same code paths as the generated copy. On the static path the org's
# credential is shared by its users (agent mode); per-user tokens belong to the vendors client. Keys are minted by the Owner
# (key create is an Owner or Admin action), one per person.
#
# The "a" in 97a keeps it out of run_all.sh's [0-9][0-9]_*.sh glob: it needs Django, Postgres and Redis.
#
#   SCN_REDIS=<host:port> SCN_REDIS_DB=<n> GATE_DJANGO_MANAGE=<manage.py wrapper> \
#     [CN30A_UPSTREAM_HOST=<dotted DNS name of this machine>] [CN30A_RECEIPT=<path>] \
#     bash scripts/scenarios/97a_cn_release_proof_static.sh
#
# The wrapper is a gate's manage.py wrapper (its own Postgres database and Redis DB, as
# gates/lib/django_live.sh describes). Tools: go, curl, jq, python3, openssl.
set -u
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
export SCN_LANE=${SCN_LANE:-cn30a-static}
# shellcheck source=gates/lib/django_live.sh
source "$SCRIPT_DIR/gates/lib/django_live.sh"
# shellcheck source=lib_mcp.sh
source "$SCRIPT_DIR/lib_mcp.sh"

FIX=$SCN_ROOT/fixtures/CN30a
CN30A_STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
CN30A_RECEIPT=${CN30A_RECEIPT:-$SCN_HOME/cn30a-receipt.json}
CN30A_FACTS=$SCN_HOME/cn30a-facts.jsonl
CN30A_SECTIONS=$SCN_HOME/cn30a-sections.txt
export SCN_FAIL_FILE=${SCN_FAIL_FILE:-$SCN_HOME/cn30a-failed.txt}
mkdir -p "$SCN_HOME"
: >"$CN30A_FACTS"
: >"$CN30A_SECTIONS"
: >"$SCN_FAIL_FILE"
TLS_DIR=$SCN_HOME/cn30a-tls
CAT_DIR=$SCN_HOME/cn30a-catalog
TLS_PID=""
DJ_PID=""

# --- receipt -------------------------------------------------------------------------------------

# fact <key> <json>: one fact for the receipt (a later fact of the same key replaces an earlier one).
fact() {
  python3 -I -c 'import json, sys; print(json.dumps({"k": sys.argv[1], "v": json.loads(sys.argv[2])}))' \
    "$1" "$2" >>"$CN30A_FACTS"
}

# section <id> <claim> <title>: start a section; the ones before it passed.
section() {
  printf '%s\t%s\t%s\n' "$1" "$2" "$3" >>"$CN30A_SECTIONS"
  scn_check "$1. $3"
}

cn30a_receipt() {
  local status=$1
  mkdir -p "$(dirname "$CN30A_RECEIPT")"
  python3 -I - "$status" "$CN30A_RECEIPT" "$CN30A_FACTS" "$CN30A_SECTIONS" "$SCN_FAIL_FILE" \
    "$SCN_GATEWAY_DIR" "$CN30A_STARTED" "$SCN_ASSERTIONS" "${OMEGA_EXEC_SOURCE_HEAD:-}" "$SCN_DIR" <<'PY' || true
import datetime, json, subprocess, sys
status, out, facts_file, sections_file, fail_file, repo, started, assertions, source_head, scn_dir = sys.argv[1:]
def git(*args):
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""
facts = {}
for line in open(facts_file, encoding="utf-8"):
    if line.strip():
        row = json.loads(line)
        facts[row["k"]] = row["v"]
sections = [line.rstrip("\n").split("\t") for line in open(sections_file, encoding="utf-8") if line.strip()]
failed = open(fail_file, encoding="utf-8").read().strip() or None
passed = status == "0" and failed is None
rows = []
for i, (sid, claim, title) in enumerate(sections):
    last = i == len(sections) - 1
    rows.append({"id": sid, "claim": claim, "title": title, "result": "pass" if passed or not last else "fail"})
claims = {}
for row in rows:
    if row["claim"] != "-":
        claims[row["claim"]] = "fail" if row["result"] == "fail" or claims.get(row["claim"]) == "fail" else "pass"
required = ["org_isolation", "ssrf_refusals", "one_record_per_call", "write_tools_need_approval", "kill_switch",
            "no_credential_exposed"]
for claim in required:
    claims.setdefault(claim, "not_reached")
receipt = {
    "schema": "cn30a-release-proof-static/v1",
    "lane": "S2-P CN30a (MCP-APPS-PLAN 2.3)",
    "script": "agentcc-gateway/scripts/scenarios/97a_cn_release_proof_static.sh",
    "result": "pass" if passed and all(claims[c] == "pass" for c in required) else "fail",
    "exit_status": int(status),
    "commit": git("rev-parse", "HEAD"),
    "tree": git("rev-parse", "HEAD^{tree}"),
    "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
    "worktree_changes": len([l for l in git("status", "--porcelain").splitlines() if l.strip()]),
    "source_head": source_head or None,
    "started_at": started,
    "finished_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "assertions": int(assertions or 0),
    "failed_assertion": failed,
    "claims": claims,
    "sections": rows,
    "scn_dir": scn_dir,
}
receipt.update(facts)
with open(out, "w", encoding="utf-8") as f:
    json.dump(receipt, f, indent=2, sort_keys=False)
    f.write("\n")
print("cn30a: receipt %s (%s)" % (out, receipt["result"]))
PY
}

dj_down() { [ -n "$DJ_PID" ] && kill "$DJ_PID" 2>/dev/null; DJ_PID=""; return 0; }
tls_down() { [ -n "$TLS_PID" ] && kill "$TLS_PID" 2>/dev/null; TLS_PID=""; return 0; }
# Every EXIT trap lib_mcp.sh arms starts with _scn_mcp_down (before scn_up, and '_scn_mcp_down; scn_down'
# after it), so the receipt is written there, once, with the script's exit status ($? on entry), and
# Django and the TLS pipe stop with the mocks.
eval "_cn30a_lib_mcp_down() $(declare -f _scn_mcp_down | sed '1d')"
_scn_mcp_down() {
  local status=$?
  if [ -z "${CN30A_RECEIPT_DONE:-}" ]; then
    CN30A_RECEIPT_DONE=1
    cn30a_receipt "$status"
    dj_down
    tls_down
  fi
  _cn30a_lib_mcp_down
}

# --- helpers -------------------------------------------------------------------------------------

sha256() { python3 -I -c 'import hashlib, sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$1"; }
LIST='{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"cn30a","version":"1"}}}'
CHAT='{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}'

# keep <tag>: keep the last response (headers and body) for the credential scan of section 10.
keep() {
  local n
  n=$(find "$SCN_DIR/kept" -type f 2>/dev/null | wc -l | tr -d ' ')
  cp "$SCN_DIR/last.body" "$SCN_DIR/kept/$(printf '%05d' "$n")-$1.body" 2>/dev/null
  cp "$SCN_DIR/last.headers" "$SCN_DIR/kept/$(printf '%05d' "$((n + 1))")-$1.headers" 2>/dev/null
  return 0
}

# The four people: A1 (org A's Owner), A2 (org A's Member), B1, B2. Their session tokens, orgs and
# workspaces are TOK_*, ORG_OF_*, WS_OF_*; the gateway key each uses is KEY_* (the Owner mints both).
keyof() { eval "printf '%s' \"\${KEY_$1}\""; }
orgof() { eval "printf '%s' \"\${ORG_OF_$1}\""; }

# dj <who> <method> <path> [json]: a console call through Django's dev server as one person (session
# token, X-Organization-Id and X-Workspace-Id as the console sends them; DJ_ORG and DJ_WS replace the
# last two). Prints the status; the response is in $SCN_DIR/last.{body,headers} and kept.
dj() {
  local who=$1 method=$2 path=$3 body=${4:-} tok org ws code
  eval "tok=\${TOK_$who} org=\${ORG_OF_$who} ws=\${WS_OF_$who}"
  local args=(-sS -o "$SCN_DIR/last.body" -D "$SCN_DIR/last.headers" -w '%{http_code}' -X "$method" "$DJ_URL$path"
    -H "Authorization: Bearer $tok" -H "X-Organization-Id: ${DJ_ORG:-$org}" -H "X-Workspace-Id: ${DJ_WS:-$ws}")
  [ -n "$body" ] && args+=(-H 'Content-Type: application/json' --data-binary "$body")
  code=$(curl "${args[@]}")
  keep "dj-$who"
  echo "$code"
}

# throttle <org tag>: Test connection and catalogue adds share 10 a minute per org (MCPProbeOrgThrottle);
# wait so the next one is at most the ninth in any 62 s.
throttle() {
  local f=$SCN_HOME/cn30a-throttle-$1 now oldest
  now=$(date +%s)
  touch "$f"
  awk -v now="$now" '$1 > now - 62' "$f" >"$f.tmp" && mv "$f.tmp" "$f"
  if [ "$(wc -l <"$f" | tr -d ' ')" -ge 9 ]; then
    oldest=$(head -n 1 "$f")
    echo "  (throttle: org $1 waits $((oldest + 62 - now)) s for the probe budget)"
    sleep $((oldest + 62 - now))
  fi
  date +%s >>"$f"
}

# mcp_open <key> <path>: initialize a legacy session (2025-06-18) on path. Sets OPEN_CODE, and
# SCN_MCP_SESSION when it is 200 (empty otherwise); the caller unsets it.
mcp_open() {
  SCN_MCP_SESSION=""
  OPEN_CODE=$(SCN_KEY=$1 scn_mcp_post "$2" "$INIT")
  keep open
  if [ "$OPEN_CODE" = 200 ]; then
    SCN_MCP_SESSION=$(scn_hdr Mcp-Session-Id)
    [ -n "$SCN_MCP_SESSION" ] || scn_fail mcp_open "initialize on $2 set no Mcp-Session-Id"
    SCN_KEY=$1 scn_mcp_post "$2" '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
  fi
  export SCN_MCP_SESSION
}

# mcp_list <who> <path>: LIST_CODE and LISTED (the tool names, sorted, comma-joined) in a new session;
# LIST_CODE is the initialize status when that is not 200.
mcp_list() {
  local key
  key=$(keyof "$1")
  mcp_open "$key" "$2"
  if [ "$OPEN_CODE" != 200 ]; then
    LIST_CODE=$OPEN_CODE LISTED=""
    unset SCN_MCP_SESSION
    return 0
  fi
  LIST_CODE=$(SCN_KEY=$key scn_mcp_post "$2" "$LIST")
  keep "list-$1"
  LISTED=$(jq -r '[.result.tools[]?.name] | sort | join(",")' <"$SCN_DIR/last.body" 2>/dev/null)
  unset SCN_MCP_SESSION
}

# mcp_init_code <who> <path>: the HTTP status of an initialize by the person's key (no session kept).
mcp_init_code() {
  local key code
  key=$(keyof "$1")
  code=$(SCN_MCP_SESSION="" SCN_KEY=$key scn_mcp_post "$2" "$INIT")
  keep "init-$1"
  echo "$code"
}

CALLS=$SCN_HOME/cn30a-calls.tsv
: >"$CALLS"
# mcp_call <who> <path> <tool> [arguments]: one tools/call by the person's key in a new session. Sets
# CALL_CODE, CALL_TEXT (the result's first text) and CALL_ERR: "none" for a result, "<code> <data.code>
# <rule>" for a JSON-RPC error (-32602 is an unknown or hidden tool), "isError <code> <rule>" for a
# refusal in band (tool_denied and the like, in _meta io.futureagi/error). Every call the gateway answered (HTTP 200) goes in calls.tsv with its x-agentcc-request-id and
# org, for section 8.
mcp_call() {
  local who=$1 path=$2 tool=$3 arguments=${4:-'{}'} key body rid
  key=$(keyof "$who")
  mcp_open "$key" "$path"
  if [ "$OPEN_CODE" != 200 ]; then
    CALL_CODE=$OPEN_CODE CALL_TEXT="" CALL_ERR="initialize $OPEN_CODE"
    unset SCN_MCP_SESSION
    return 0
  fi
  body=$(jq -nc --arg t "$tool" --argjson a "$arguments" '{jsonrpc:"2.0",id:3,method:"tools/call",params:{name:$t,arguments:$a}}')
  CALL_CODE=$(SCN_KEY=$key scn_mcp_post "$path" "$body")
  keep "call-$who"
  rid=$(scn_hdr x-agentcc-request-id)
  CALL_TEXT=$(jq -r '.result.content[0].text // ""' <"$SCN_DIR/last.body" 2>/dev/null)
  CALL_ERR=$(jq -r 'if .error then "\(.error.code) \(.error.data.code // "-") \(.error.data.metadata.rule // "-")"
    elif .result.isError == true then (.result._meta["io.futureagi/error"] // {}) as $e
      | "isError \($e.code // "-") \($e.metadata.rule // "-")"
    else "none" end' \
    <"$SCN_DIR/last.body" 2>/dev/null)
  if [ "$CALL_CODE" = 200 ]; then
    [ -n "$rid" ] || scn_fail call.request_id "the gateway answered a tools/call with no x-agentcc-request-id"
    printf '%s\t%s\t%s\t%s\n' "$rid" "$(orgof "$who")" "$tool" "$who" >>"$CALLS"
  fi
  unset SCN_MCP_SESSION
}

received() { scn_mcp_received "$1" 0 "${2:-}" | jq '.received | length'; }
# traffic <mock>: the messages the mock got other than the pool's health pings (mcp ping every
# PingInterval), so a count moves only when something was sent to it.
traffic() { echo $(($(received "$1") - $(received "$1" ping))); }
# fp_of <mock> [method]: whose credential the mock's last message (of that method) carried, by the SHA-256
# the mock keeps: a-notes, b-notes, a-docs or b-docs; none without one, other for any other value. The log
# gets these labels, never a hash.
fp_of() {
  local h
  h=$(scn_mcp_received "$1" 1 "${2:-}" | jq -r '.received[0].auth.sha256 // ""')
  case "$h" in
    "") echo none ;;
    "$FP_A_NOTES") echo a-notes ;;
    "$FP_B_NOTES") echo b-notes ;;
    "$FP_A_DOCS") echo a-docs ;;
    "$FP_B_DOCS") echo b-docs ;;
    *) echo other ;;
  esac
}
# in_body <text>: true when the last response holds the text (each org's tools name and describe only
# that org's purge or archive).
in_body() { grep -qF "$1" "$SCN_DIR/last.body" && echo true || echo false; }
# refusal: "refused" when the last mcp_call was refused, as an unknown tool (-32602) or in band
# (isError tool_denied); else what it got.
refusal() {
  case "$CALL_ERR" in
    "-32602 "*|"isError tool_denied "*) echo refused ;;
    *) echo "not refused ($CALL_ERR)" ;;
  esac
}

# wait_for <name> <timeout s> <condition>: poll the shell condition every 0.5 s; fail the step on timeout.
wait_for() {
  local name=$1 deadline=$(($(date +%s) + $2))
  shift 2
  until eval "$*"; do
    [ "$(date +%s)" -ge "$deadline" ] &&
      scn_fail "$name" "still false after the wait: $*; last body: $(head -c 800 "$SCN_DIR/last.body" 2>/dev/null)"
    sleep 0.5
  done
}

org_server_healthy() { # <org> <server>: the gateway's pool reports the server healthy
  scn_admin GET "/-/mcp/status?org_id=$1" >/dev/null
  [ "$(jq -r --arg s "$2" '[.servers[]? | select(.server_id == $s) | .healthy] | any' "$SCN_DIR/last.body" 2>/dev/null)" = true ]
}

# --- 0. the stack --------------------------------------------------------------------------------

w3_need_django
for tool in openssl go curl jq python3; do
  command -v "$tool" >/dev/null || scn_fail setup "$tool is required"
done
RUN=$(python3 -I -c 'import secrets; print(secrets.token_hex(6))')
# Fake values made for this run only; none is written to a committed file.
A_NOTES_TOKEN=scn-cn30a-a-notes-$RUN-not-a-secret
B_NOTES_KEY=scn-cn30a-b-notes-$RUN-not-a-secret
A_DOCS_KEY=scn-cn30a-a-docs-$RUN-not-a-secret
B_DOCS_KEY=scn-cn30a-b-docs-$RUN-not-a-secret
C_DOCS_KEY=scn-cn30a-c-docs-$RUN-not-a-secret # section 9's add with MCP off, refused
WEBHOOK_SECRET=scn-cn30a-webhook-$RUN-not-a-secret
# What each upstream should see: the mocks keep a credential header as its SHA-256 only.
FP_A_NOTES=$(sha256 "Bearer $A_NOTES_TOKEN")
FP_B_NOTES=$(sha256 "$B_NOTES_KEY")
FP_A_DOCS=$(sha256 "Bearer $A_DOCS_KEY")
FP_B_DOCS=$(sha256 "Bearer $B_DOCS_KEY")

# The catalogue app's upstream is https on a dotted DNS name of this machine: the catalog takes only
# https URLs, and Django's add only a DNS host. The name must resolve to an address this machine binds.
UPSTREAM_HOST=${CN30A_UPSTREAM_HOST:-$(hostname -f 2>/dev/null || hostname)}
UPSTREAM_HOST=$(echo "$UPSTREAM_HOST" | tr '[:upper:]' '[:lower:]')
UPSTREAM_IP=$(python3 -I - "$UPSTREAM_HOST" <<'PY'
import re, socket, sys
host = sys.argv[1]
if not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?([.][a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+", host):
    sys.exit(0)
try:
    ips = sorted({a[4][0] for a in socket.getaddrinfo(host, None, socket.AF_INET)})
except OSError:
    sys.exit(0)
for ip in ips:
    s = socket.socket()
    try:
        s.bind((ip, 0))
        print(ip)
        break
    except OSError:
        pass
    finally:
        s.close()
PY
)
[ -n "$UPSTREAM_IP" ] || scn_fail setup "'$UPSTREAM_HOST' is not a dotted DNS name resolving to an address of this machine;" \
  "set CN30A_UPSTREAM_HOST to one"
TLS_PORT=$(python3 -I -c 'import socket, sys; s = socket.socket(); s.bind((sys.argv[1], 0)); print(s.getsockname()[1]); s.close()' "$UPSTREAM_IP")
fact upstream_tls "$(jq -nc --arg h "$UPSTREAM_HOST" --arg ip "$UPSTREAM_IP" --arg p "$TLS_PORT" \
  '{host: $h, address: $ip, port: ($p | tonumber), mechanism: "agentcc-mcpmock on 127.0.0.1 behind a byte-level TLS pipe (python3 ssl) on the host address; a run-local CA (openssl) is trusted by the gateway alone through SSL_CERT_FILE; mcp.egress.private_allow lists the name:port"}')"

scn_mcp_up a --fixtures "$FIX/mcp/a" --auth "bearer:$A_NOTES_TOKEN"
scn_mcp_up b --fixtures "$FIX/mcp/b" --auth-header X-Notes-Key
scn_mcp_up ops --fixtures "$FIX/mcp/ops"
scn_mcp_up docs --fixtures "$FIX/mcp/docs" --public-url "https://$UPSTREAM_HOST:$TLS_PORT"

# The TLS pipe: a run-local CA and a leaf for the name, then python3's ssl in front of the docs mock.
rm -rf "$TLS_DIR"
mkdir -p "$TLS_DIR"
printf '%s\n' '[req]' 'distinguished_name = dn' 'prompt = no' 'x509_extensions = v3' '[dn]' 'CN = CN30a scenario CA' \
  '[v3]' 'basicConstraints = critical,CA:TRUE' 'keyUsage = critical,keyCertSign,cRLSign' 'subjectKeyIdentifier = hash' \
  >"$TLS_DIR/ca.cnf"
printf 'subjectAltName = DNS:%s\nextendedKeyUsage = serverAuth\nbasicConstraints = critical,CA:FALSE\nkeyUsage = critical,digitalSignature,keyEncipherment\n' \
  "$UPSTREAM_HOST" >"$TLS_DIR/leaf.ext"
{
  openssl req -x509 -config "$TLS_DIR/ca.cnf" -newkey rsa:2048 -nodes -keyout "$TLS_DIR/ca.key" -out "$TLS_DIR/ca.pem" -days 2 &&
    openssl req -new -newkey rsa:2048 -nodes -keyout "$TLS_DIR/leaf.key" -out "$TLS_DIR/leaf.csr" -subj "/CN=$UPSTREAM_HOST" &&
    openssl x509 -req -in "$TLS_DIR/leaf.csr" -CA "$TLS_DIR/ca.pem" -CAkey "$TLS_DIR/ca.key" -CAcreateserial \
      -out "$TLS_DIR/leaf.pem" -days 2 -sha256 -extfile "$TLS_DIR/leaf.ext"
} >"$TLS_DIR/openssl.log" 2>&1 || scn_fail setup "openssl could not make the run's CA and leaf: $(tail -5 "$TLS_DIR/openssl.log")"
SYSTEM_ROOTS=""
for f in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/cert.pem; do
  [ -f "$f" ] && { SYSTEM_ROOTS=$f; break; }
done
cat ${SYSTEM_ROOTS:+"$SYSTEM_ROOTS"} "$TLS_DIR/ca.pem" >"$TLS_DIR/roots.pem"
cat >"$TLS_DIR/pipe.py" <<'PY'
# The scenario's TLS pipe: TLS in, the same bytes out to 127.0.0.1:<backend>, both ways (no HTTP parsing, so
# streams and long polls pass as they are). It logs connections and handshake failures, never data.
import os, socket, ssl, sys, threading
ip, port, cert, key, backend, ready = sys.argv[1:7]
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.minimum_version = ssl.TLSVersion.TLSv1_2
ctx.load_cert_chain(cert, key)
srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind((ip, int(port)))
srv.listen(128)
with open(ready + ".tmp", "w") as f:
    f.write(port)
os.replace(ready + ".tmp", ready)
def pump(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
def handle(raw, n):
    try:
        tls = ctx.wrap_socket(raw, server_side=True)
    except (OSError, ssl.SSLError) as e:
        print("conn %d: handshake failed (%s)" % (n, type(e).__name__), flush=True)
        raw.close()
        return
    try:
        up = socket.create_connection(("127.0.0.1", int(backend)), timeout=10)
        up.settimeout(None)
    except OSError as e:
        print("conn %d: backend unreachable (%s)" % (n, type(e).__name__), flush=True)
        tls.close()
        return
    print("conn %d: open" % n, flush=True)
    t = threading.Thread(target=pump, args=(up, tls), daemon=True)
    t.start()
    pump(tls, up)
    t.join()
    tls.close()
    up.close()
n = 0
while True:
    conn, _ = srv.accept()
    n += 1
    threading.Thread(target=handle, args=(conn, n), daemon=True).start()
PY
python3 -I "$TLS_DIR/pipe.py" "$UPSTREAM_IP" "$TLS_PORT" "$TLS_DIR/leaf.pem" "$TLS_DIR/leaf.key" "$SCN_MCP_DOCS_PORT" \
  "$TLS_DIR/ready" >"$TLS_DIR/pipe.log" 2>&1 &
TLS_PID=$!
deadline=$(($(date +%s) + 15))
until [ -s "$TLS_DIR/ready" ]; do
  kill -0 "$TLS_PID" 2>/dev/null || scn_fail setup "the TLS pipe exited: $(tail -5 "$TLS_DIR/pipe.log")"
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail setup "the TLS pipe did not start"
  sleep 0.1
done
code=$(curl -sS -o /dev/null -w '%{http_code}' --cacert "$TLS_DIR/ca.pem" "https://$UPSTREAM_HOST:$TLS_PORT/__health")
[ "$code" = 200 ] || scn_fail setup "https://$UPSTREAM_HOST:$TLS_PORT/__health through the pipe answered $code"

# The catalogue: the fixture app rendered with the pipe's port, then agentcc-catalog's own build (the
# schema, L0, L1 and the open-data rule), which writes the bundle and its Django copy.
HEAD_SHA=$(git -C "$SCN_GATEWAY_DIR" rev-parse HEAD)
rm -rf "$CAT_DIR"
mkdir -p "$CAT_DIR/apps/cn30a-docs/providers" "$CAT_DIR/platforms"
sed "s/@@CN30A_TLS_PORT@@/$TLS_PORT/g" "$FIX/catalog/apps/cn30a-docs/app.yaml.in" >"$CAT_DIR/apps/cn30a-docs/app.yaml"
sed "s/@@CN30A_TLS_PORT@@/$TLS_PORT/g" "$FIX/catalog/apps/cn30a-docs/providers/key.yaml.in" \
  >"$CAT_DIR/apps/cn30a-docs/providers/key.yaml"
grep -q '@@CN30A_' "$CAT_DIR/apps/cn30a-docs/providers/key.yaml" && scn_fail setup "unrendered placeholders in the catalog"
(cd "$SCN_GATEWAY_DIR" && go run ./cmd/agentcc-catalog build --catalog "$CAT_DIR" --django-copy "$CAT_DIR/django-catalog.json" \
  --date "$(date -u +%Y-%m-%d)" --source-commit "$HEAD_SHA") >"$SCN_HOME/cn30a-catalog-build.log" 2>&1 ||
  scn_fail setup "agentcc-catalog build refused the fixture catalog: $(tail -20 "$SCN_HOME/cn30a-catalog-build.log")"
[ -s "$CAT_DIR/django-catalog.json" ] || scn_fail setup "agentcc-catalog build wrote no Django copy"
BUNDLE_VERSION=$(jq -r .version "$CAT_DIR/django-catalog.json")
fact catalog "$(jq -c '{fixture: "agentcc-gateway/scripts/scenarios/fixtures/CN30a/catalog", app: "cn30a-docs", provider: "key",
  bundle_version: .version, bundle_sha256: .sha256, bundle_source_commit: .sourceCommit,
  built_by: "go run ./cmd/agentcc-catalog build --catalog <scratch> --django-copy <scratch>/django-catalog.json",
  gateway_loads: "connectors.catalog_dir", django_reads: "agentcc.services.connector_catalog.BUNDLE_PATH"}' "$CAT_DIR/django-catalog.json")"

DJ_PORT=$(_scn_free_port)
DJ_URL=http://127.0.0.1:$DJ_PORT
mkdir -p "$SCN_BIN"
TEMPLATE=$SCN_BIN/cn30a-gateway.yaml
sed -e "s#@@CN30A_DJANGO_URL@@#$DJ_URL#" -e "s#@@CN30A_WEBHOOK_SECRET@@#$WEBHOOK_SECRET#" \
  -e "s#@@CN30A_CATALOG_DIR@@#$CAT_DIR#" -e "s/@@CN30A_A_PORT@@/$SCN_MCP_A_PORT/" -e "s/@@CN30A_B_PORT@@/$SCN_MCP_B_PORT/" \
  -e "s/@@CN30A_OPS_PORT@@/$SCN_MCP_OPS_PORT/" -e "s/@@CN30A_UPSTREAM_HOST@@/$UPSTREAM_HOST/" \
  -e "s/@@CN30A_TLS_PORT@@/$TLS_PORT/" "$FIX/gateway.yaml" >"$TEMPLATE"
grep -v '^ *#' "$TEMPLATE" | grep -q '@@CN30A_' && scn_fail setup "unrendered placeholders in $TEMPLATE"
scn_mcp_gateway_template "$TEMPLATE"
# Only the gateway trusts the run's CA.
export SSL_CERT_FILE=$TLS_DIR/roots.pem
SCN_CATALOG_WAIT_SKIP=all scn_up
unset SSL_CERT_FILE
mkdir -p "$SCN_DIR/kept"
python3 -I - "$SCN_DIR/gateway.log" <<'PY' || scn_fail setup "the gateway did not load the fixture catalog from connectors.catalog_dir"
import json, sys
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    try:
        row = json.loads(line)
    except ValueError:
        continue
    if row.get("msg") == "connector catalog loaded" and "connectors.catalog_dir" in str(row.get("source", "")):
        sys.exit(0)
sys.exit(1)
PY
bs_django_up
fact database "$(jq -c '{name: .database, created_by_this_run: .created}' "$SCN_DIR/bs-db_create.json")"
env AGENTCC_GATEWAY_INTERNAL_URL="$SCN_GW" AGENTCC_ADMIN_TOKEN="$SCN_ADMIN_TOKEN" AGENTCC_WEBHOOK_SECRET="$WEBHOOK_SECRET" \
  NO_STARTUP_DB_MUTATIONS=false CN30A_BUNDLE="$CAT_DIR/django-catalog.json" \
  bash "$GATE_DJANGO_MANAGE" shell -c "
import os
from pathlib import Path
from django.core.management import call_command
from agentcc.services import connector_catalog
connector_catalog.BUNDLE_PATH = Path(os.environ['CN30A_BUNDLE'])
call_command('runserver', '127.0.0.1:$DJ_PORT', use_reloader=False)
" >"$SCN_DIR/django-server.log" 2>&1 &
DJ_PID=$!
deadline=$(($(date +%s) + 180))
until curl -sS -o /dev/null "$DJ_URL/agentcc/api-keys/bulk/" 2>/dev/null; do
  kill -0 "$DJ_PID" 2>/dev/null || scn_fail setup "Django's dev server exited: $(tail -20 "$SCN_DIR/django-server.log")"
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail setup "Django's dev server did not answer in 180 s"
  sleep 1
done
code=$(curl -sS -o "$SCN_DIR/last.body" -w '%{http_code}' -H "Authorization: Bearer $SCN_ADMIN_TOKEN" "$DJ_URL/agentcc/api-keys/bulk/")
scn_assert_eq 0.django_serves_the_gateway 200 "$code"
fact stack "$(jq -nc --arg gw "$SCN_GW" --arg dj "$DJ_URL" '{gateway: ("agentcc built from this tree, " + $gw),
  control_plane: ("Django dev server (runserver) on Postgres and Redis, " + $dj + "; keys and org configs synced every 2 s; tool_call logs through /agentcc/webhook/logs/"),
  upstreams: "agentcc-mcpmock: a (org A, bearer), b (org B, api_key header X-Notes-Key), ops (operator YAML), docs (catalogue app, behind TLS)",
  orgs: 2, people_per_org: 2, roles: ["Owner", "Member"]}')"
fact deviations '[
  "The catalogue is a fixture app (fixtures/CN30a/catalog), not the real bundle: its provider is agentcc-mcpmock behind a TLS pipe on this machine, because the real providers are vendor hosts with no test accounts here. agentcc-catalog builds its bundle and Django copy; the gateway loads it as connectors.catalog_dir and the Django dev server reads it through connector_catalog.BUNDLE_PATH, set before runserver.",
  "The catalogue app takes its host as a URL variable (mockHost) with a permissive pattern, so the add path can be pointed at metadata and IP literals; the real catalogue pins each variable host to the vendor domain.",
  "On the static path an org credential is shared by the org people (agent mode); per-user tokens and their fingerprints belong to the vendors client.",
  "Gateway keys are minted by the org Owner for both people (creating a key is an Owner or Admin action).",
  "The gateway trusts the run CA through SSL_CERT_FILE (its own environment only); the operator mcp.enabled false is proven by restarting the gateway on the same port with that line flipped."
]'

# --- 1. orgs, people and keys --------------------------------------------------------------------

section 1 - "two orgs, an Owner and a Member each; the Owner mints a key per person, the Member cannot"
bs_org "CN30a org A $(date -u +%Y-%m-%dT%H:%M:%SZ)"
ORG_A=$BS_ORG
bs_org "CN30a org B $(date -u +%Y-%m-%dT%H:%M:%SZ)"
ORG_B=$BS_ORG
bs_django_py people "
import secrets
from django.utils import timezone
from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.provider_credential import AgentccProviderCredential
from integrations.services.credentials import CredentialManager
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
def person(org, tag, role, level):
    user = User.objects.create_user(email=f'cn30a-{tag}-{secrets.token_hex(4)}@example.com',
        password=secrets.token_urlsafe(16), name=f'CN30a {tag}', organization=org, organization_role=role)
    membership = OrganizationMembership.no_workspace_objects.create(user=user, organization=org, role=role,
        level=level, is_active=True)
    return user, membership
def token(user):
    access = AuthToken.objects.create(user=user, auth_type=AuthTokenType.ACCESS.value, is_active=True,
        last_used_at=timezone.now())
    return generate_encrypted_message({'user_id': str(user.id), 'id': str(access.id)})
out = {}
for tag, org_id in (('A', BS_ARGS[0]), ('B', BS_ARGS[1])):
    org = Organization.objects.get(pk=org_id)
    owner, owner_m = person(org, tag.lower() + '1', OrganizationRoles.OWNER, Level.OWNER)
    ws = Workspace.no_workspace_objects.create(name=f'CN30a {tag}', organization=org, is_default=True,
        is_active=True, created_by=owner)
    WorkspaceMembership.no_workspace_objects.create(user=owner, workspace=ws, role='Workspace Owner',
        level=Level.OWNER, organization_membership=owner_m, is_active=True)
    member, member_m = person(org, tag.lower() + '2', OrganizationRoles.MEMBER, Level.MEMBER)
    WorkspaceMembership.no_workspace_objects.create(user=member, workspace=ws,
        role=OrganizationRoles.WORKSPACE_MEMBER, level=Level.WORKSPACE_MEMBER,
        organization_membership=member_m, is_active=True)
    # The org's own key on the operator's provider (BYOK), so its keys may chat.
    AgentccProviderCredential.no_workspace_objects.create(organization=org, provider_name='operator-openai',
        encrypted_credentials=CredentialManager.encrypt({'api_key': 'scn-test-org-byok-key'}),
        api_format='openai', models_list=['gpt-4o'])
    out[tag] = {'ws': str(ws.pk),
        'p1': {'id': str(owner.id), 'email': owner.email, 'token': token(owner)},
        'p2': {'id': str(member.id), 'email': member.email, 'token': token(member)}}
bs_out(out)" "$ORG_A" "$ORG_B"
P=$SCN_DIR/bs-people.json
for o in A B; do
  for n in 1 2; do
    eval "TOK_$o$n=\$(jq -r .$o.p$n.token \"\$P\") EMAIL_$o$n=\$(jq -r .$o.p$n.email \"\$P\") UID_$o$n=\$(jq -r .$o.p$n.id \"\$P\")"
    eval "ORG_OF_$o$n=\$ORG_$o WS_OF_$o$n=\$(jq -r .$o.ws \"\$P\")"
  done
done
WS_B=$WS_OF_B1
rm -f "$P" # it holds the session tokens
for who in A1 A2 B1 B2; do
  [ -n "$(eval "printf '%s' \"\${TOK_$who}\"")" ] || scn_fail setup "no session token for $who"
done
mint() { # <owner> <for whom>: the Owner mints a key for the person; sets KEY_<whom> and KID_<whom>
  local email code
  eval "email=\${EMAIL_$2}"
  code=$(curl -sS -o "$SCN_DIR/mint.json" -w '%{http_code}' -X POST "$DJ_URL/agentcc/api-keys/" \
    -H "Authorization: Bearer $(eval "printf '%s' \"\${TOK_$1}\"")" -H "X-Organization-Id: $(orgof "$1")" \
    -H "X-Workspace-Id: $(eval "printf '%s' \"\${WS_OF_$1}\"")" -H 'Content-Type: application/json' \
    --data-binary "$(jq -nc --arg n "cn30a-$(echo "$2" | tr '[:upper:]' '[:lower:]')" --arg e "$email" '{name: $n, owner: $e}')")
  scn_assert_eq "1.$1_mints_for_$2" 201 "$code"
  eval "KEY_$2=\$(jq -r .result.key \"\$SCN_DIR/mint.json\") KID_$2=\$(jq -r .result.gateway_key_id \"\$SCN_DIR/mint.json\")"
  rm -f "$SCN_DIR/mint.json" # the creation answer is the one place a raw key may be
}
mint A1 A1
mint A1 A2
mint B1 B1
mint B1 B2
scn_assert_eq 1.member_cannot_mint 403 "$(dj A2 POST /agentcc/api-keys/ '{"name":"cn30a-a2-self"}')"
scn_assert_eq 1.other_member_cannot_mint 403 "$(dj B2 POST /agentcc/api-keys/ '{"name":"cn30a-b2-self"}')"
for who in A1 A2 B1 B2; do
  kid=$(eval "printf '%s' \"\${KID_$who}\"")
  wait_for "1.gateway_holds_key_$who" 30 "[ \"\$(scn_admin GET /-/keys/$kid)\" = 200 ]"
  keep "admin-key-$who"
done
scn_assert_eq 1.gateway_holds_the_four_keys ok ok
fact people "$(jq -nc --arg a "$ORG_A" --arg b "$ORG_B" '{org_a: $a, org_b: $b,
  per_org: [{person: "1", role: "Owner (gateway admin)"}, {person: "2", role: "Member (reads only)"}],
  keys: "one gateway key per person, minted by the org Owner through POST /agentcc/api-keys/; synced to the gateway"}')"

# --- 2. org-saved servers ------------------------------------------------------------------------

section 2 org_isolation "org-saved servers through the console, credentials in the vault; access.default deny until a virtual server"
A_SERVER=$(jq -nc --arg u "$SCN_MCP_A_URL/mcp" --arg t "$A_NOTES_TOKEN" \
  '{server_id: "notes", config: {url: $u, transport: "http", auth: {type: "bearer", token: $t}}}')
B_SERVER=$(jq -nc --arg u "$SCN_MCP_B_URL/mcp" --arg k "$B_NOTES_KEY" \
  '{server_id: "notes", config: {url: $u, transport: "http", auth: {type: "api_key", key: $k, header: "X-Notes-Key"}}}')
scn_assert_eq 2.member_cannot_add_a_server 403 "$(dj A2 POST /agentcc/gateways/default/update-mcp-server/ "$A_SERVER")"
scn_assert_eq 2.a_owner_adds_notes 200 "$(dj A1 POST /agentcc/gateways/default/update-mcp-server/ "$A_SERVER")"
scn_assert_jq 2.a_add_synced '.result.gateway_synced' true
scn_assert_eq 2.b_owner_adds_notes 200 "$(dj B1 POST /agentcc/gateways/default/update-mcp-server/ "$B_SERVER")"
scn_assert_jq 2.b_add_synced '.result.gateway_synced' true
scn_assert_eq 2.a_switches_mcp_on 200 "$(dj A1 POST /agentcc/gateways/default/update-config/ '{"mcp":{"enabled":true}}')"
scn_assert_eq 2.b_switches_mcp_on 200 "$(dj B1 POST /agentcc/gateways/default/update-config/ '{"mcp":{"enabled":true}}')"
wait_for 2.a_notes_healthy 30 "org_server_healthy $ORG_A notes"
wait_for 2.b_notes_healthy 30 "org_server_healthy $ORG_B notes"
scn_assert_eq 2.a_mock_got_a_vault_credential a-notes "$(fp_of a)"
scn_assert_eq 2.b_mock_got_b_vault_credential b-notes "$(fp_of b)"
a_calls=$(received a tools/call)
mcp_list A1 /mcp
scn_assert_eq 2.default_deny_lists_nothing "200 " "$LIST_CODE $LISTED"
mcp_call A1 /mcp notes__lookup
scn_assert_eq 2.default_deny_refuses_the_call "200 isError tool_denied access.default" "$CALL_CODE $CALL_ERR"
mcp_call A1 /mcp notes__purge
scn_assert_eq 2.default_deny_refuses_the_write "200 isError tool_denied access.default" "$CALL_CODE $CALL_ERR"
scn_assert_eq 2.default_deny_reached_no_upstream "$a_calls" "$(received a tools/call)"
TEAM='{"mcp":{"virtual_servers":{"team":{"title":"Team notes","members":["notes"],"access":{"default_for_org":true}}}}}'
scn_assert_eq 2.a_default_virtual_server 200 "$(dj A1 POST /agentcc/gateways/default/update-config/ "$TEAM")"
scn_assert_eq 2.b_default_virtual_server 200 "$(dj B1 POST /agentcc/gateways/default/update-config/ "$TEAM")"
wait_for 2.a_team_lists 30 'mcp_list A1 /mcp; [ "$LISTED" = "notes__lookup,notes__purge" ]'
wait_for 2.b_team_lists 30 'mcp_list B1 /mcp; [ "$LISTED" = "notes__archive,notes__lookup" ]'
fact fingerprints_notes "$(jq -nc --arg a "$FP_A_NOTES" --arg b "$FP_B_NOTES" '{org_a_notes_sha256: $a, org_b_notes_sha256: $b}')"

# --- 3. isolation of org-saved servers -----------------------------------------------------------

section 3 org_isolation "each org's keys list and call only its own server, with its own credential"
a_calls=$(received a tools/call)
b_calls=$(received b tools/call)
for who in A1 A2; do
  mcp_list "$who" /mcp
  scn_assert_eq "3.${who}_lists_a_only" "200 notes__lookup,notes__purge" "$LIST_CODE $LISTED"
  mcp_call "$who" /mcp notes__lookup '{"q":"x"}'
  scn_assert_eq "3.${who}_lookup_answered_by_a" "200 org-a notes" "$CALL_CODE $CALL_TEXT"
  scn_assert_eq "3.${who}_lookup_carried_a_credential" a-notes "$(fp_of a tools/call)"
done
for who in B1 B2; do
  mcp_list "$who" /mcp
  scn_assert_eq "3.${who}_lists_b_only" "200 notes__archive,notes__lookup" "$LIST_CODE $LISTED"
  mcp_call "$who" /mcp notes__lookup '{"q":"x"}'
  scn_assert_eq "3.${who}_lookup_answered_by_b" "200 org-b notes" "$CALL_CODE $CALL_TEXT"
  scn_assert_eq "3.${who}_lookup_carried_b_credential" b-notes "$(fp_of b tools/call)"
done
scn_assert_eq 3.a_mock_saw_only_a_calls "$((a_calls + 2))" "$(received a tools/call)"
scn_assert_eq 3.b_mock_saw_only_b_calls "$((b_calls + 2))" "$(received b tools/call)"
mcp_call B1 /mcp notes__purge
scn_assert_eq 3.b_cannot_call_a_tool "200 -32602" "$CALL_CODE ${CALL_ERR%% *}"
mcp_call A2 /mcp notes__archive
scn_assert_eq 3.a_cannot_call_b_tool "200 -32602" "$CALL_CODE ${CALL_ERR%% *}"
mcp_call A1 /mcp ops__restart
scn_assert_eq 3.no_org_reaches_the_operator_server "200 -32602" "$CALL_CODE ${CALL_ERR%% *}"
scn_assert_eq 3.operator_server_untouched 0 "$(received ops tools/call)"
scn_assert_eq 3.cross_calls_reached_no_upstream "$((a_calls + 2)) $((b_calls + 2))" "$(received a tools/call) $(received b tools/call)"
SCN_KEY=$KEY_A1 scn_mcp_session_init 2025-06-18 /mcp
SESSION_A=$SCN_MCP_SESSION
SCN_MCP_SESSION=$SESSION_A
scn_assert_eq 3.a_session_is_404_for_b 404 "$(SCN_KEY=$KEY_B1 scn_mcp_post /mcp "$LIST")"
SCN_MCP_SESSION=$SESSION_A
scn_assert_eq 3.a_session_still_a 200 "$(SCN_KEY=$KEY_A1 scn_mcp_post /mcp "$LIST")"
unset SCN_MCP_SESSION
scn_assert_eq 3.a_member_reads_tools 200 "$(dj A2 GET /agentcc/gateways/default/mcp-tools/)"
scn_assert_eq 3.a_console_tools_are_a "true false" "$(in_body purge) $(in_body archive)"
scn_assert_eq 3.b_member_reads_tools 200 "$(dj B2 GET /agentcc/gateways/default/mcp-tools/)"
scn_assert_eq 3.b_console_tools_are_b "true false" "$(in_body archive) $(in_body purge)"
# A's Owner names org B in X-Organization-Id: Django resolves the org A belongs to (accounts
# _resolve_organization), so the read shows A's tools and the write lands on A.
scn_assert_eq 3.foreign_org_header_read 200 "$(DJ_ORG=$ORG_B DJ_WS=$WS_B dj A1 GET /agentcc/gateways/default/mcp-tools/)"
scn_assert_eq 3.foreign_org_header_reads_own_org "true false" "$(in_body purge) $(in_body archive)"
versions() {
  bs_django_py versions "
from agentcc.models import AgentccOrgConfig
bs_out({o: AgentccOrgConfig.no_workspace_objects.get(organization_id=o, is_active=True, deleted=False).version
        for o in BS_ARGS})" "$ORG_A" "$ORG_B"
}
versions
VA=$(jq -r --arg o "$ORG_A" '.[$o]' "$SCN_DIR/bs-versions.json")
VB=$(jq -r --arg o "$ORG_B" '.[$o]' "$SCN_DIR/bs-versions.json")
scn_assert_eq 3.foreign_org_header_write 200 \
  "$(DJ_ORG=$ORG_B DJ_WS=$WS_B dj A1 POST /agentcc/gateways/default/update-config/ '{"mcp":{"enabled":true}}')"
versions
scn_assert_eq 3.foreign_org_header_write_lands_on_own_org "$((VA + 1)) $VB" \
  "$(jq -r --arg a "$ORG_A" --arg b "$ORG_B" '"\(.[$a]) \(.[$b])"' "$SCN_DIR/bs-versions.json")"
scn_assert_eq 3.a_owner_lists_credentials 200 "$(dj A1 GET /agentcc/mcp/credentials/)"
CRED_A_NOTES=$(jq -r '[.. | objects | select(.server_id? == "notes") | .id // .credential_id // empty][0] // ""' "$SCN_DIR/last.body")
[ -n "$CRED_A_NOTES" ] || scn_fail 3.a_credential_id "no credential id for notes in A's list: $(head -c 600 "$SCN_DIR/last.body")"
scn_assert_eq 3.b_owner_lists_credentials 200 "$(dj B1 GET /agentcc/mcp/credentials/)"
scn_assert_eq 3.b_list_has_no_a_credential false "$(grep -qF "$CRED_A_NOTES" "$SCN_DIR/last.body" && echo true || echo false)"
a_all=$(traffic a)
throttle B
scn_assert_eq 3.b_probe_with_a_credential_404 404 \
  "$(dj B1 POST /agentcc/mcp/probe/ "$(jq -nc --arg u "$SCN_MCP_A_URL/mcp" --arg c "$CRED_A_NOTES" '{url: $u, credential_id: $c}')")"
scn_assert_jq 3.b_probe_with_a_credential_code '.code' mcp_credential_not_found
scn_assert_eq 3.b_probe_reached_no_upstream "$a_all" "$(traffic a)"
scn_assert_eq 3.gateway_config_read 200 "$(scn_admin GET "/-/orgs/$ORG_A/config")"
keep admin-config-a
scn_assert_jq 3.gateway_config_masks_the_secret '.mcp.servers.notes.credential.secret.value' '****'
scn_assert_jq 3.gateway_config_keeps_the_vault_reference '.mcp.servers.notes.credential.credential_id' "$CRED_A_NOTES"

# --- 4. SSRF ---------------------------------------------------------------------------------------

section 4 ssrf_refusals "metadata, private and unlisted loopback addresses are refused"
# What a refused request could have reached: the org servers' and the operator server's tools, and the
# catalogue app's listing (an add lists its tools first).
reachable() { echo "$(received a tools/call) $(received b tools/call) $(received ops tools/call) $(received docs tools/list) $(received docs tools/call)"; }
before_all=$(reachable)
SSRF_ROWS=()
probe_refused() { # <name> <url>: A's Owner's Test connection answers ok:false egress_refused
  throttle A
  scn_assert_eq "$1.status" 200 "$(dj A1 POST /agentcc/mcp/probe/ "$(jq -nc --arg u "$2" '{url: $u, transport: "http"}')")"
  scn_assert_jq "$1.refused" '"\(.result.ok) \(.result.error)"' "false egress_refused"
  SSRF_ROWS+=("$(jq -nc --arg u "$2" '{path: "Test connection (POST /agentcc/mcp/probe/)", target: $u, result: "ok:false egress_refused"}')")
}
probe_refused 4.metadata_ip_http "http://169.254.169.254/computeMetadata/v1/"
probe_refused 4.metadata_ip_https "https://169.254.169.254/computeMetadata/v1/"
probe_refused 4.metadata_ipv4_mapped "http://[::ffff:169.254.169.254]/computeMetadata/v1/"
if [ -n "${CN30A_METADATA_NAME_RECEIPT:-}" ]; then
  # 746e: under the outer dial guard this ONE row runs separately, since
  # that guard refuses the name before the product can resolve it. Keep the
  # row here and require its exact same-head refusal receipt, never a skip.
  python3 -I - "$CN30A_METADATA_NAME_RECEIPT" "$HEAD_SHA" <<'PY' || scn_fail 4.metadata_name.refused "missing or mismatched separate-invocation proof"
import json, sys
r = json.load(open(sys.argv[1]))
assert r["head"] == sys.argv[2]
assert r["assertion"] == "4.metadata_name.refused"
assert r["product"] == {"ok": False, "error": "unreachable"}
assert r["records"] == [{"host": "metadata.google.internal", "port": "80",
                         "class": "cloud-metadata", "kind": "dial-hostname-refused"}]
assert r["counted_attempts"] == 1 and r["refused"] is True
PY
  scn_assert_eq 4.metadata_name.refused separate-invocation-exact separate-invocation-exact
  SSRF_ROWS+=('{"path":"Test connection (separate invocation, 746e)","target":"http://metadata.google.internal/computeMetadata/v1/","result":"ok:false unreachable; exactly one dial-hostname-refused record; product DNS refusal proven separately by Test746eProbeMetadataNamesAfterLoopbackDNS"}')
  META_NAME=metadata.google.internal
elif getent hosts metadata.google.internal 2>/dev/null | grep -q '^169\.254\.169\.254'; then
  probe_refused 4.metadata_name "http://metadata.google.internal/computeMetadata/v1/"
  META_NAME=metadata.google.internal
else
  META_NAME=""
fi
probe_refused 4.private_10 "http://10.20.30.40/mcp"
probe_refused 4.private_192_168 "http://192.168.0.10/mcp"
probe_refused 4.unlisted_loopback "${SCN_MOCK}/mcp" # the HTTP mock: live, loopback, not in private_allow
fact metadata_name_row "$(jq -nc --arg n "$META_NAME" --arg separate "${CN30A_METADATA_NAME_RECEIPT:-}" 'if $separate != "" then "run separately (746e): exact refused name dial and guard-masked unreachable; product post-resolution refusal covered by Go loopback-DNS test" elif $n == "" then "not run: metadata.google.internal does not resolve to 169.254.169.254 here" else "run: metadata.google.internal resolves to 169.254.169.254 here" end')"
# A catalogue add whose host is a URL variable: B's Owner, before B adds the app.
add_body() { jq -nc --arg h "$1" --arg k "$2" '{url_variables: {mockHost: $h}, credential: {value: $k}}'; }
if [ -n "$META_NAME" ]; then
  throttle B
  scn_assert_eq 4.add_with_metadata_host 502 "$(dj B1 POST /agentcc/connectors/apps/cn30a-docs/add/ "$(add_body "$META_NAME" "$B_DOCS_KEY")")"
  scn_assert_jq 4.add_with_metadata_host_code '.code' connector_tool_listing_failed
  scn_assert_eq 4.add_with_metadata_host_names_the_guard true \
    "$(jq -r '(.message // .result // "") | tostring | contains("egress_refused")' "$SCN_DIR/last.body")"
  SSRF_ROWS+=("$(jq -nc --arg h "$META_NAME" '{path: "catalogue add (url_variables.mockHost)", target: $h, result: "502 connector_tool_listing_failed (egress_refused at the probe)"}')")
fi
throttle B
scn_assert_eq 4.add_with_metadata_ip 400 "$(dj B1 POST /agentcc/connectors/apps/cn30a-docs/add/ "$(add_body 169.254.169.254 "$B_DOCS_KEY")")"
scn_assert_jq 4.add_with_metadata_ip_code '.code' url_variable_invalid
SSRF_ROWS+=("$(jq -nc '{path: "catalogue add (url_variables.mockHost)", target: "169.254.169.254", result: "400 url_variable_invalid (an IP literal is not a DNS host)"}')")
bs_django_py b_after_refused_adds "
from agentcc.models import AgentccOrgConfig
from agentcc.models.mcp_credential import AgentccMCPCredential
mcp = AgentccOrgConfig.no_workspace_objects.get(organization_id=BS_ARGS[0], is_active=True, deleted=False).mcp or {}
bs_out({'server': 'cn30a-docs' in (mcp.get('servers') or {}),
        'vault_rows': AgentccMCPCredential.objects.filter(organization_id=BS_ARGS[0], server_id='cn30a-docs').count()})" "$ORG_B"
scn_assert_jq 4.refused_adds_wrote_nothing '"\(.server) \(.vault_rows)"' "false 0" "$SCN_DIR/bs-b_after_refused_adds.json"
# An org-saved server at the metadata address: refused at the save, or saved and never connected.
META_SERVER='{"server_id":"meta","config":{"url":"http://169.254.169.254/computeMetadata/v1/","transport":"http"}}'
code=$(dj A1 POST /agentcc/gateways/default/update-mcp-server/ "$META_SERVER")
if [ "$code" = 200 ]; then
  deadline=$(($(date +%s) + 30))
  until python3 -I - "$SCN_DIR/gateway.log" <<'PY'
import json, sys
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    try:
        row = json.loads(line)
    except ValueError:
        continue
    if row.get("msg") == "mcp egress refused a connection" and str(row.get("address", "")).startswith("169.254.169.254") \
            and row.get("reason") == "forbidden":
        sys.exit(0)
sys.exit(1)
PY
  do
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail 4.org_saved_metadata_refused_at_dial "no egress refusal of 169.254.169.254 in the gateway log"
    sleep 1
  done
  scn_assert_eq 4.org_saved_metadata_refused_at_dial ok ok
  scn_assert_eq 4.org_saved_metadata_never_healthy false "$(org_server_healthy "$ORG_A" meta && echo true || echo false)"
  scn_admin GET "/-/mcp/status?org_id=$ORG_A" >/dev/null
  scn_assert_jq 4.org_saved_metadata_no_tools '[.servers[]? | select(.server_id == "meta") | .tool_count // 0] | add // 0' 0
  scn_assert_eq 4.org_saved_metadata_removed 200 "$(dj A1 POST /agentcc/gateways/default/remove-mcp-server/ '{"server_id":"meta"}')"
  fact org_saved_metadata_branch '"saved (200); the gateway refused the dial (egress guard, reason forbidden), the server never became healthy and listed no tool; removed after the check"'
else
  scn_assert_eq 4.org_saved_metadata_refused_at_save 400 "$code"
  fact org_saved_metadata_branch "$(jq -c '{refused_at_save: 400, code: .code}' "$SCN_DIR/last.body")"
fi
SSRF_ROWS+=("$(jq -nc '{path: "org-saved server (update-mcp-server)", target: "http://169.254.169.254/computeMetadata/v1/", result: "see org_saved_metadata_branch"}')")
scn_assert_eq 4.no_refusal_reached_an_upstream "$before_all" "$(reachable)"
scn_assert_eq 4.unlisted_loopback_never_reached 0 "$(scn_received 0 /mcp | jq '.data | length')"
fact ssrf_rows "$(printf '%s\n' "${SSRF_ROWS[@]}" | jq -sc .)"

# --- 5. catalogue adds ----------------------------------------------------------------------------

section 5 org_isolation "catalogue adds: each org's connector, key and virtual server are its own"
scn_assert_eq 5.a_member_reads_the_catalog 200 "$(dj A2 GET /agentcc/connectors/catalog/)"
scn_assert_jq 5.catalog_is_the_built_bundle '.catalog.version' "$BUNDLE_VERSION"
scn_assert_jq 5.app_listed_not_connected '[.results[] | select(.id == "cn30a-docs") | .connected] | map(tostring) | join(",")' false
scn_assert_eq 5.member_cannot_add 403 "$(dj A2 POST /agentcc/connectors/apps/cn30a-docs/add/ "$(add_body "$UPSTREAM_HOST" "$A_DOCS_KEY")")"
throttle A
scn_assert_eq 5.a_owner_adds 201 "$(dj A1 POST /agentcc/connectors/apps/cn30a-docs/add/ "$(add_body "$UPSTREAM_HOST" "$A_DOCS_KEY")")"
cp "$SCN_DIR/last.body" "$SCN_DIR/add-a.json"
scn_assert_eq 5.a_add_listed_with_a_key a-docs "$(fp_of docs tools/list)"
VS_A=$(jq -r .result.virtual_server.id "$SCN_DIR/add-a.json")
scn_assert_jq 5.a_add_synced '"\(.result.gateway_synced) \(.result.credential.connected) \(.result.mcp_enabled)"' "true true true" "$SCN_DIR/add-a.json"
scn_assert_jq 5.a_add_baseline '"\(.result.baseline.tool_count) \(.result.baseline.tools_hash | test("^[0-9a-f]{64}$"))"' "5 true" "$SCN_DIR/add-a.json"
throttle B
scn_assert_eq 5.b_owner_adds 201 "$(dj B1 POST /agentcc/connectors/apps/cn30a-docs/add/ "$(add_body "$UPSTREAM_HOST" "$B_DOCS_KEY")")"
cp "$SCN_DIR/last.body" "$SCN_DIR/add-b.json"
scn_assert_eq 5.b_add_listed_with_b_key b-docs "$(fp_of docs tools/list)"
VS_B=$(jq -r .result.virtual_server.id "$SCN_DIR/add-b.json")
scn_assert_eq 5.two_virtual_servers true "$([ -n "$VS_A" ] && [ "$VS_A" != null ] && [ "$VS_A" != "$VS_B" ] && echo true || echo false)"
scn_assert_eq 5.a_catalog_after 200 "$(dj A2 GET /agentcc/connectors/catalog/)"
scn_assert_jq 5.a_catalog_shows_a_connector '[.results[] | select(.id == "cn30a-docs") | "\(.connected) \(.servers[0].virtual_server.id)"][0]' "true $VS_A"
scn_assert_eq 5.b_catalog_after 200 "$(dj B2 GET /agentcc/connectors/catalog/)"
scn_assert_jq 5.b_catalog_shows_b_connector '[.results[] | select(.id == "cn30a-docs") | "\(.connected) \(.servers[0].virtual_server.id)"][0]' "true $VS_B"
DOCS=/mcp/v/cn30a-docs
# The connector serves all five tools: the two read-only ones, and the three that run after an approval.
# The provider declares no tools and its defaultPosture is ask, so the annotations decide.
DOCS_TOOLS=cn30a-docs__annotate_doc,cn30a-docs__delete_doc,cn30a-docs__get_doc,cn30a-docs__search_docs,cn30a-docs__update_doc
wait_for 5.a_connector_lists 45 'mcp_list A1 $DOCS; [ "$LISTED" = "$DOCS_TOOLS" ]'
wait_for 5.b_connector_lists 45 'mcp_list B1 $DOCS; [ "$LISTED" = "$DOCS_TOOLS" ]'
docs_calls=$(received docs tools/call)
for who in A1 A2 B1 B2; do
  case $who in A*) fp=a-docs ;; *) fp=b-docs ;; esac
  mcp_list "$who" "$DOCS"
  scn_assert_eq "5.${who}_lists_every_tool" "200 $DOCS_TOOLS" "$LIST_CODE $LISTED"
  mcp_call "$who" "$DOCS" cn30a-docs__search_docs '{"q":"x"}'
  scn_assert_eq "5.${who}_search_answered" "200 docs search" "$CALL_CODE $CALL_TEXT"
  scn_assert_eq "5.${who}_search_carried_own_org_key" "$fp" "$(fp_of docs tools/call)"
done
scn_assert_eq 5.docs_mock_saw_four_calls "$((docs_calls + 4))" "$(received docs tools/call)"
# A virtual server is resolved by the chain (hook 4), so a foreign id is the HTTP 404 of its first call.
mcp_list B1 "/mcp/v/$VS_A"
scn_assert_eq 5.a_vs_id_is_404_for_b "404 " "$LIST_CODE $LISTED"
mcp_list A1 "/mcp/v/$VS_B"
scn_assert_eq 5.b_vs_id_is_404_for_a "404 " "$LIST_CODE $LISTED"
mcp_list A2 "/mcp/v/$VS_A"
scn_assert_eq 5.a_own_vs_id_serves_a "200 $DOCS_TOOLS" "$LIST_CODE $LISTED"
scn_assert_eq 5.a_credentials_after 200 "$(dj A1 GET /agentcc/mcp/credentials/)"
A_CRED_IDS=$(jq -r '[.. | objects | select(.server_id? != null) | .id // .credential_id // empty] | unique | join(",")' "$SCN_DIR/last.body")
scn_assert_eq 5.b_credentials_after 200 "$(dj B1 GET /agentcc/mcp/credentials/)"
B_CRED_IDS=$(jq -r '[.. | objects | select(.server_id? != null) | .id // .credential_id // empty] | unique | join(",")' "$SCN_DIR/last.body")
scn_assert_eq 5.each_org_two_credentials "2 2" "$(echo "$A_CRED_IDS" | tr ',' '\n' | grep -c .) $(echo "$B_CRED_IDS" | tr ',' '\n' | grep -c .)"
scn_assert_eq 5.no_credential_shared "" "$(comm -12 <(echo "$A_CRED_IDS" | tr ',' '\n' | sort) <(echo "$B_CRED_IDS" | tr ',' '\n' | sort) | tr '\n' ' ' | sed 's/ $//')"
fact fingerprints_docs "$(jq -nc --arg a "$FP_A_DOCS" --arg b "$FP_B_DOCS" --arg va "$VS_A" --arg vb "$VS_B" \
  '{org_a_docs_sha256: $a, org_b_docs_sha256: $b, org_a_virtual_server: $va, org_b_virtual_server: $vb}')"

# --- 6. write tools need an approval --------------------------------------------------------------

section 6 write_tools_need_approval "a catalogue add allows read-only tools and runs the others after an approval; their calls are refused in band, never sent"
posture() { jq -r --arg n "$1" '[.result.tools[] | select(.name == $n) | "\(.gateway_posture) \(.reason // "-")"][0]' "$SCN_DIR/add-a.json"; }
scn_assert_eq 6.search_docs_allowed "allow -" "$(posture search_docs)"
scn_assert_eq 6.get_doc_allowed "allow -" "$(posture get_doc)"
scn_assert_eq 6.update_doc_needs_approval "allow approval_required" "$(posture update_doc)"
scn_assert_eq 6.delete_doc_needs_approval "allow approval_required" "$(posture delete_doc)"
scn_assert_eq 6.unannotated_needs_approval "allow approval_required" "$(posture annotate_doc)"
scn_assert_eq 6.b_add_same_posture "$(jq -c '[.result.tools[] | [.name, .gateway_posture, .reason]]' "$SCN_DIR/add-a.json")" \
  "$(jq -c '[.result.tools[] | [.name, .gateway_posture, .reason]]' "$SCN_DIR/add-b.json")"
docs_calls=$(received docs tools/call)
GATED_AS=()
# Each call stores a pending approval (require listed, validity once, the gateway's 900 s default) and
# is answered in band: isError, _meta io.futureagi/error code approval_required with the approval's id.
# Nothing here decides an approval, so none of these calls runs.
for tool in update_doc delete_doc annotate_doc; do
  for who in A1 B2; do
    mcp_call "$who" "$DOCS" "cn30a-docs__$tool"
    scn_assert_eq "6.${who}_${tool}_needs_approval" "200 isError approval_required -" "$CALL_CODE $CALL_ERR"
    scn_assert_jq "6.${who}_${tool}_names_a_pending_approval" \
      '.result._meta["io.futureagi/error"].metadata | "\(.status) \(.approval_id | type)"' "pending string"
    GATED_AS+=("$(jq -nc --arg w "$who" --arg t "$tool" --arg e "$CALL_ERR" '{who: $w, tool: $t, answer: $e}')")
  done
done
mcp_call A2 /mcp cn30a-docs__delete_doc
scn_assert_eq 6.not_through_the_org_default "200 refused" "$CALL_CODE $(refusal)"
scn_assert_eq 6.no_gated_tool_reached_the_upstream "$docs_calls" "$(received docs tools/call)"
fact write_tools "$(jq -c --argjson calls "$(printf '%s\n' "${GATED_AS[@]}" | jq -sc .)" '{posture_at_add: [.result.tools[] | {name, gateway_posture, reason}],
  approval_gated_calls: $calls,
  org_saved_default: "access.default deny until the org binds a virtual server (section 2: tool_denied, rule access.default)"}' "$SCN_DIR/add-a.json")"

# --- 7. kill switch: an org's mcp.enabled false -----------------------------------------------

section 7 kill_switch "org A switches MCP off: its servers stop, org B runs on"
scn_assert_eq 7.a_switches_mcp_off 200 "$(dj A1 POST /agentcc/gateways/default/update-config/ '{"mcp":{"enabled":false}}')"
wait_for 7.a_lists_nothing 30 'mcp_list A1 /mcp; [ "$LISTED" = "" ]'
before="$(traffic a) $(received docs tools/call)"
mcp_list A2 "$DOCS"
scn_assert_eq 7.a_connector_lists_nothing "" "$LISTED"
mcp_call A1 /mcp notes__lookup
scn_assert_eq 7.a_call_refused true "$([ "$CALL_ERR" != none ] && echo true || echo "false ($CALL_CODE $CALL_TEXT)")"
mcp_call A2 "$DOCS" cn30a-docs__search_docs
scn_assert_eq 7.a_connector_call_refused true "$([ "$CALL_ERR" != none ] && echo true || echo "false ($CALL_CODE $CALL_TEXT)")"
scn_assert_eq 7.a_upstreams_untouched "$before" "$(traffic a) $(received docs tools/call)"
b_calls=$(received b tools/call)
mcp_call B1 /mcp notes__lookup
scn_assert_eq 7.b_runs_on "200 org-b notes" "$CALL_CODE $CALL_TEXT"
mcp_call B2 "$DOCS" cn30a-docs__get_doc
scn_assert_eq 7.b_connector_runs_on "200 docs get" "$CALL_CODE $CALL_TEXT"
scn_assert_eq 7.b_connector_with_b_key b-docs "$(fp_of docs tools/call)"
scn_assert_eq 7.b_upstream_counted "$((b_calls + 1))" "$(received b tools/call)"

# --- 8. one record per call -----------------------------------------------------------------------

section 8 one_record_per_call "exactly one tool_call row per answered tools/call, in its own org"
b_admin_before=$(received b tools/call)
scn_assert_eq 8.admin_playground 200 "$(dj B1 POST /agentcc/gateways/default/test-mcp-tool/ '{"name":"notes__lookup","arguments":{}}')"
scn_assert_jq 8.admin_playground_is_error '.result.is_error // false' false
scn_assert_eq 8.admin_playground_on_b "$((b_admin_before + 1))" "$(received b tools/call)"
n_a=$(awk -F'\t' -v o="$ORG_A" '$2 == o' "$CALLS" | wc -l | tr -d ' ')
n_b=$(awk -F'\t' -v o="$ORG_B" '$2 == o' "$CALLS" | wc -l | tr -d ' ')
api_rows() { # <who>: the org's tool_call rows through the console (request_id principal_kind id), one per line
  dj "$1" GET '/agentcc/mcp/tool-calls/?limit=100' >/dev/null
  jq -r '.results[]? | "\(.request_id) \(.principal_kind) \(.id)"' "$SCN_DIR/last.body"
}
wait_for 8.a_rows_ingested 90 '[ "$(api_rows A2 | grep -vc " admin ")" -ge "$n_a" ]'
wait_for 8.b_rows_ingested 90 '[ "$(api_rows B2 | grep -c " admin ")" -ge 1 ] && [ "$(api_rows B2 | grep -vc " admin ")" -ge "$n_b" ]'
sleep 6 # one more flush: a duplicate would land now
api_rows A2 >"$SCN_DIR/api-rows-a.txt"
api_rows B2 >"$SCN_DIR/api-rows-b.txt"
# same_ids <org> <rows file>: the org's answered calls and its console rows are the same request ids, each
# once ("<n> same"); the ids stay out of the log.
same_ids() {
  local calls rows
  calls=$(awk -F'\t' -v o="$1" '$2 == o {print $1}' "$CALLS" | sort)
  rows=$(awk '$2 != "admin" {print $1}' "$2" | sort)
  if [ "$calls" = "$rows" ]; then echo "$(echo "$calls" | grep -c .) same"; else echo "differ: $(diff <(echo "$calls") <(echo "$rows") | grep -c '^[<>]') ids"; fi
}
scn_assert_eq 8.a_console_rows_are_a_calls "$n_a same" "$(same_ids "$ORG_A" "$SCN_DIR/api-rows-a.txt")"
scn_assert_eq 8.b_console_rows_are_b_calls "$n_b same" "$(same_ids "$ORG_B" "$SCN_DIR/api-rows-b.txt")"
scn_assert_eq 8.admin_rows "0 1" "$(grep -c ' admin ' "$SCN_DIR/api-rows-a.txt") $(grep -c ' admin ' "$SCN_DIR/api-rows-b.txt")"
cut -f1-3 "$CALLS" >"$SCN_DIR/calls-for-db.tsv"
bs_django_py records "
import collections
from agentcc.models.mcp_tool_call import AgentccToolCall
calls = [line.rstrip('\n').split('\t') for line in open(BS_ARGS[0]) if line.strip()]
rows = AgentccToolCall.objects.filter(organization_id__in=BS_ARGS[1:3])
by_rid = collections.defaultdict(list)
for r in rows:
    by_rid[r.request_id].append(r)
bad = []
for rid, org, tool in calls:
    found = by_rid.get(rid, [])
    if len(found) != 1 or str(found[0].organization_id) != org or found[0].tool != tool:
        bad.append({'request_id': rid, 'rows': len(found), 'orgs': [str(r.organization_id) for r in found],
                    'tools': [r.tool for r in found], 'want_org': org, 'want_tool': tool})
key_rows = [r for r in rows if r.principal_kind != 'admin']
bs_out({'calls': len(calls), 'bad': bad, 'key_rows': len(key_rows), 'admin_rows': len(rows) - len(key_rows),
        'distinct_request_ids': len({r.request_id for r in key_rows}),
        'outcomes': dict(collections.Counter(r.outcome for r in rows))})" "$SCN_DIR/calls-for-db.tsv" "$ORG_A" "$ORG_B"
R=$SCN_DIR/bs-records.json
echo "  rows: $(jq -c '{calls, key_rows, admin_rows, outcomes}' "$R")"
scn_assert_jq 8.each_call_one_row_in_its_org '.bad | length' 0 "$R"
scn_assert_jq 8.rows_equal_calls '"\(.key_rows) \(.distinct_request_ids)"' "$((n_a + n_b)) $((n_a + n_b))" "$R"
scn_assert_jq 8.one_admin_row '.admin_rows' 1 "$R"
A_ROW=$(awk 'NR == 1 {print $3}' "$SCN_DIR/api-rows-a.txt")
scn_assert_eq 8.a_row_detail_for_a 200 "$(dj A2 GET "/agentcc/mcp/tool-calls/$A_ROW/")"
scn_assert_eq 8.a_row_detail_404_for_b 404 "$(dj B2 GET "/agentcc/mcp/tool-calls/$A_ROW/")"
fact tool_calls "$(jq -c --argjson a "$n_a" --argjson b "$n_b" '{answered_calls: .calls, org_a_calls: $a, org_b_calls: $b,
  key_rows: .key_rows, admin_rows: .admin_rows, outcomes: .outcomes,
  matched_by: "x-agentcc-request-id of each answered tools/call, in the database and through GET /agentcc/mcp/tool-calls/"}' "$R")"

# --- 9. kill switch: the operator's mcp.enabled false ----------------------------------------------

section 9 kill_switch "the operator switches MCP off (restart): /mcp, Django's MCP reads and the catalogue add go, chat stays"
rows_before="$(api_rows A2 | wc -l | tr -d ' ') $(api_rows B2 | wc -l | tr -d ' ')"
sed 's/^  enabled: true # cn30a-kill-switch$/  enabled: false # cn30a-kill-switch/' "$SCN_DIR/config.yaml" >"$SCN_DIR/config-mcp-off.yaml"
grep -q '^  enabled: false # cn30a-kill-switch$' "$SCN_DIR/config-mcp-off.yaml" || scn_fail 9.config "no marked mcp.enabled line in the config"
gw_port=${SCN_GW##*:}
kill "$SCN_GW_PID" 2>/dev/null
i=0
while kill -0 "$SCN_GW_PID" 2>/dev/null && [ $i -lt 75 ]; do sleep 0.2; i=$((i + 1)); done
kill -9 "$SCN_GW_PID" 2>/dev/null
unset_env=()
for var in $(env | sed -n 's/^\(AGENTCC_[A-Za-z0-9_]*\)=.*/\1/p'); do unset_env+=(-u "$var"); done
env ${unset_env[@]+"${unset_env[@]}"} SSL_CERT_FILE="$TLS_DIR/roots.pem" AGENTCC_PORT="$gw_port" \
  "$SCN_BIN/agentcc" --config "$SCN_DIR/config-mcp-off.yaml" >"$SCN_DIR/gateway-mcp-off.log" 2>&1 &
SCN_GW_PID=$!
_scn_wait_http gateway-mcp-off "$SCN_GW/healthz" 30 "$SCN_GW_PID"
for who in A1 B1; do
  kid=$(eval "printf '%s' \"\${KID_$who}\"")
  wait_for "9.restarted_gateway_holds_key_$who" 30 "[ \"\$(scn_admin GET /-/keys/$kid)\" = 200 ]"
done
# With MCP off the gateway holds no MCP client, so from here no upstream may get anything at all.
up_before="$(received a) $(received b) $(received docs) $(received ops)"
for who in A1 A2 B1 B2; do
  scn_assert_eq "9.${who}_mcp_404" 404 "$(mcp_init_code "$who" /mcp)"
  scn_assert_eq "9.${who}_connector_404" 404 "$(mcp_init_code "$who" "$DOCS")"
done
scn_assert_eq 9.admin_mcp_status_404 404 "$(scn_admin GET "/-/mcp/status?org_id=$ORG_B")"
scn_assert_eq 9.console_tools_disabled 404 "$(dj B2 GET /agentcc/gateways/default/mcp-tools/)"
scn_assert_jq 9.console_tools_disabled_code '.code' gateway_mcp_disabled
scn_assert_eq 9.console_playground_disabled 404 "$(dj B1 POST /agentcc/gateways/default/test-mcp-tool/ '{"name":"notes__lookup","arguments":{}}')"
scn_assert_jq 9.console_playground_disabled_code '.code' gateway_mcp_disabled
# The Integrations page reads this status and shows the MCP page's off state, with no app, Connect or Custom
# MCP action (IntegrationsSection.jsx). The catalogue list API still answers: the page, not the API, hides it.
scn_assert_eq 9.console_status_read 200 "$(dj B2 GET /agentcc/gateways/default/mcp-status/)"
scn_assert_jq 9.console_status_says_mcp_off '"\(.result.enabled) \(.result.reason)"' "false gateway_mcp_disabled"
scn_assert_eq 9.catalogue_list_still_answers 200 "$(dj B2 GET /agentcc/connectors/catalog/)"
scn_assert_jq 9.catalogue_list_still_lists_the_app '[.results[] | select(.id == "cn30a-docs")] | length' 1
# An add needs the gateway's probe, which MCP off takes away: org C, which has added nothing (A and B have,
# and an add of an added app is 409 before the probe), is refused 404 gateway_mcp_disabled and nothing is
# written. Its Owner's key and token join section 10's scan.
bs_org "CN30a org C $(date -u +%Y-%m-%dT%H:%M:%SZ)"
ORG_C=$BS_ORG
bs_django_py person_c "
import secrets
from django.utils import timezone
from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
org = Organization.objects.get(pk=BS_ARGS[0])
owner = User.objects.create_user(email=f'cn30a-c1-{secrets.token_hex(4)}@example.com',
    password=secrets.token_urlsafe(16), name='CN30a c1', organization=org, organization_role=OrganizationRoles.OWNER)
owner_m = OrganizationMembership.no_workspace_objects.create(user=owner, organization=org, role=OrganizationRoles.OWNER,
    level=Level.OWNER, is_active=True)
ws = Workspace.no_workspace_objects.create(name='CN30a C', organization=org, is_default=True, is_active=True,
    created_by=owner)
WorkspaceMembership.no_workspace_objects.create(user=owner, workspace=ws, role='Workspace Owner', level=Level.OWNER,
    organization_membership=owner_m, is_active=True)
access = AuthToken.objects.create(user=owner, auth_type=AuthTokenType.ACCESS.value, is_active=True,
    last_used_at=timezone.now())
bs_out({'ws': str(ws.pk), 'token': generate_encrypted_message({'user_id': str(owner.id), 'id': str(access.id)})})" "$ORG_C"
TOK_C1=$(jq -r .token "$SCN_DIR/bs-person_c.json") ORG_OF_C1=$ORG_C WS_OF_C1=$(jq -r .ws "$SCN_DIR/bs-person_c.json")
rm -f "$SCN_DIR/bs-person_c.json" # it holds the session token
[ -n "$TOK_C1" ] && [ "$TOK_C1" != null ] || scn_fail 9.setup "no session token for C1"
throttle C
scn_assert_eq 9.add_with_mcp_off 404 "$(dj C1 POST /agentcc/connectors/apps/cn30a-docs/add/ "$(add_body "$UPSTREAM_HOST" "$C_DOCS_KEY")")"
scn_assert_jq 9.add_with_mcp_off_code '.code' gateway_mcp_disabled
scn_assert_eq 9.add_with_mcp_off_keeps_no_key false "$(in_body "$C_DOCS_KEY")"
bs_django_py c_after_add "
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
bs_out({'credentials': AgentccMCPCredential.objects.filter(organization_id=BS_ARGS[0]).count(),
    'versions': AgentccOrgConfig._base_manager.filter(organization_id=BS_ARGS[0]).count()})" "$ORG_C"
scn_assert_jq 9.add_with_mcp_off_writes_nothing '"\(.credentials) \(.versions)"' "0 0" "$SCN_DIR/bs-c_after_add.json"
scn_assert_eq 9.chat_still_served 200 "$(SCN_KEY=$KEY_B1 scn_chat "$CHAT")"
keep chat-b1
sleep 7 # a flush interval and more
scn_assert_eq 9.no_upstream_reached "$up_before" "$(received a) $(received b) $(received docs) $(received ops)"
scn_assert_eq 9.no_row_written "$rows_before" "$(api_rows A2 | wc -l | tr -d ' ') $(api_rows B2 | wc -l | tr -d ' ')"

# --- 10. no credential in any log, response, GET or table ------------------------------------------

section 10 no_credential_exposed "no credential in any log, response, GET or table"
for who in A1 B1; do
  dj "$who" GET /agentcc/mcp/credentials/ >/dev/null
  dj "$who" GET /agentcc/api-keys/ >/dev/null
  dj "$who" GET /agentcc/org-configs/active/ >/dev/null
  dj "$who" GET /agentcc/connectors/catalog/ >/dev/null
  dj "$who" GET /agentcc/connectors/catalog/cn30a-docs/ >/dev/null
  dj "$who" GET '/agentcc/mcp/tool-calls/?limit=100' >/dev/null
done
for row in $(awk '{print $3}' "$SCN_DIR/api-rows-a.txt" | head -n 5); do dj A2 GET "/agentcc/mcp/tool-calls/$row/" >/dev/null; done
for row in $(awk '{print $3}' "$SCN_DIR/api-rows-b.txt" | head -n 5); do dj B2 GET "/agentcc/mcp/tool-calls/$row/" >/dev/null; done
for path in "/-/orgs/$ORG_A/config" "/-/orgs/$ORG_B/config" /-/orgs/configs /-/keys; do
  scn_admin GET "$path" >/dev/null
  keep admin-get
done
for m in a b docs ops; do scn_mcp_received "$m" >"$SCN_DIR/kept/received-$m.json"; done
SCAN_FILES=("$SCN_DIR" "$SCN_MCP_DIR" "$TLS_DIR/pipe.log" "$SCN_HOME/cn30a-catalog-build.log")
TOKENS=()
for who in A1 A2 B1 B2 C1; do TOKENS+=("$(eval "printf '%s' \"\${TOK_$who}\"")"); done
KEYS=("$KEY_A1" "$KEY_A2" "$KEY_B1" "$KEY_B2")
# The scanners read the values from their environment only (CN30A_SECRETS, bs_django_py's BS_ARGS), so
# no file holds them.
SECRETS_JSON=$(jq -nc --arg a "$A_NOTES_TOKEN" --arg b "$B_NOTES_KEY" --arg c "$A_DOCS_KEY" --arg d "$B_DOCS_KEY" \
  --arg e "$C_DOCS_KEY" \
  --arg w "$WEBHOOK_SECRET" --arg k1 "${KEYS[0]}" --arg k2 "${KEYS[1]}" --arg k3 "${KEYS[2]}" --arg k4 "${KEYS[3]}" \
  --arg t1 "${TOKENS[0]}" --arg t2 "${TOKENS[1]}" --arg t3 "${TOKENS[2]}" --arg t4 "${TOKENS[3]}" \
  --arg t5 "${TOKENS[4]}" \
  '{a_notes_token: $a, b_notes_key: $b, a_docs_key: $c, b_docs_key: $d, c_docs_key_refused: $e, webhook_secret: $w,
    gateway_key_a1: $k1, gateway_key_a2: $k2, gateway_key_b1: $k3, gateway_key_b2: $k4,
    session_token_a1: $t1, session_token_a2: $t2, session_token_b1: $t3, session_token_b2: $t4,
    session_token_c1: $t5}')
CN30A_SECRETS=$SECRETS_JSON python3 -I - "$SCN_DIR/config.yaml" "${SCAN_FILES[@]}" >"$SCN_DIR/scan.json" <<'PY'
import json, os, sys
secrets = json.loads(os.environ["CN30A_SECRETS"])
control, roots = sys.argv[1], sys.argv[2:]
if any(len(v) < 16 for v in secrets.values()):
    sys.exit("a value to scan for is empty or short")
def read(path):
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""
# The control: the rendered config is the one file that holds the webhook secret, so the scan can see one.
control_found = secrets["webhook_secret"] in read(control)
files, hits = 0, []
for root in roots:
    paths = [root] if os.path.isfile(root) else [os.path.join(d, n) for d, _, ns in os.walk(root) for n in ns]
    for path in paths:
        if os.path.abspath(path) in (os.path.abspath(control), os.path.abspath(control.replace("config.yaml", "config-mcp-off.yaml"))):
            continue  # the gateway's own config, which must hold the webhook secret
        files += 1
        text = read(path)
        for name, value in secrets.items():
            if value in text:
                hits.append({"file": path, "secret": name})
print(json.dumps({"files": files, "hits": hits, "control_found": control_found}))
PY
scn_assert_jq 10.scan_control_sees_a_secret '.control_found' true "$SCN_DIR/scan.json"
scn_assert_jq 10.scanned_files '.files > 50' true "$SCN_DIR/scan.json"
scn_assert_jq 10.no_credential_in_logs_or_responses '.hits | map("\(.secret) in \(.file)") | join("; ")' "" "$SCN_DIR/scan.json"
bs_django_py db_scan "
import json
from django.db import connection
secrets = json.loads(BS_ARGS[0])
control = BS_ARGS[1]
hits, tables, control_found = [], 0, False
with connection.cursor() as c:
    c.execute(\"SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'\")
    names = [r[0] for r in c.fetchall()]
    for name in names:
        q = connection.ops.quote_name(name)
        tables += 1
        c.execute(f'SELECT count(*) FROM {q} t WHERE strpos(t::text, %s) > 0', [control])
        if c.fetchone()[0]:
            control_found = True
        for label, value in secrets.items():
            c.execute(f'SELECT count(*) FROM {q} t WHERE strpos(t::text, %s) > 0', [value])
            if c.fetchone()[0]:
                hits.append({'table': name, 'secret': label})
from agentcc.models.mcp_credential import AgentccMCPCredential
vault = AgentccMCPCredential.objects.filter(organization_id__in=BS_ARGS[2:4], status='active').count()
bs_out({'tables': tables, 'hits': hits, 'control_found': control_found, 'active_vault_rows': vault})" \
  "$SECRETS_JSON" "CN30a org A" "$ORG_A" "$ORG_B"
D=$SCN_DIR/bs-db_scan.json
scn_assert_jq 10.db_scan_control_sees_a_value '.control_found' true "$D"
scn_assert_jq 10.no_credential_in_any_table '.hits | map("\(.secret) in \(.table)") | join("; ")' "" "$D"
scn_assert_jq 10.vault_holds_the_four_credentials '.active_vault_rows' 4 "$D"
fact credential_scan "$(jq -c --slurpfile db "$D" '{files_scanned: .files, file_hits: (.hits | length), tables_scanned: $db[0].tables,
  table_hits: ($db[0].hits | length), active_vault_rows: $db[0].active_vault_rows,
  values: ["the two org servers upstream credentials", "the two catalogue keys", "org C s catalogue key its add with MCP off refused", "the log webhook secret", "the four gateway keys", "the five session tokens"],
  controls: "the gateway config holds the webhook secret and the org name is in the database: both found"}' "$SCN_DIR/scan.json")"
scn_pass
