#!/usr/bin/env bash
# Each person's own account on an MCP server, signed in over
# OAuth, end to end on the stack: the real gateway binary built from this tree, with MCP sign-in on;
# Django's dev server as its control plane (keys and org configs synced every 2 s, each person's token
# fetched on mcp-upstream-token-v1) and as the authorization server MCP clients sign people in with; on
# Postgres and Redis; one agentcc-mcpmock per org in OAuth mode as the vendor (an MCP server and its own
# authorization server, taking only the org's own OAuth app). Two orgs, an Owner and a Member each. Every
# MCP call is made the way an MCP client makes it: a person signs the client in through Django's
# authorization server for the org's virtual server, and the client calls with that access token.
# It proves:
#
#   own account   a person connects their own vendor account; the server's tools are then listed
#                 through that account, and their calls carry that account's token and no other.
#   auth required a person who has not connected is asked to connect when they call a tool (with a
#                 connect link made for them alone), and the vendor gets nothing for them.
#   no crossing   no token crosses between people: every call the vendor gets carries the token of the
#                 person who made it (SHA-256 fingerprints), across two people of one org and across
#                 orgs; each vendor sees only its own org's people.
#   revoke        a person's disconnect revokes their account: their next call is asked to connect
#                 again and the vendor gets nothing more for them, while the other person's calls go on.
#   no secret     no vendor token, refresh token, access token, app secret or the gateway's internal
#                 secret is in any log, response or table of the database (the vault holds ciphertext).
#
# It writes a receipt JSON naming the commit (CN30B_RECEIPT, default $SCN_HOME/cn30b-receipt.json), on a
# red too, with the failed assertion.
#
# The "b" keeps it out of run_all.sh's [0-9][0-9]_*.sh glob: like 97a and 98a it needs Django, Postgres
# and Redis.
#
#   SCN_REDIS=<host:port> SCN_REDIS_DB=<n> GATE_DJANGO_MANAGE=<manage.py wrapper> [CN30B_RECEIPT=<path>] \
#     bash scripts/scenarios/98b_cn_per_user_oauth.sh
#
# The wrapper is a gate's manage.py wrapper (its own Postgres database and Redis DB, as
# gates/lib/django_live.sh describes). Tools: go, curl, jq, python3.
set -u
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
export SCN_LANE=${SCN_LANE:-cn30b-per-user-oauth}
# The gateway keeps the sign-in verification keys under no prefix: an earlier run's set (another
# issuer, the same version) is removed before the gateway starts, so this run's own keys are taken.
export SCN_REDIS_GLOBAL_KEYS=${SCN_REDIS_GLOBAL_KEYS:-mcpoauth:keys}
# shellcheck source=gates/lib/django_live.sh
source "$SCRIPT_DIR/gates/lib/django_live.sh"
# shellcheck source=lib_mcp.sh
source "$SCRIPT_DIR/lib_mcp.sh"

FIX=$SCN_ROOT/fixtures/CN30B
CN30B_STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
CN30B_RECEIPT=${CN30B_RECEIPT:-$SCN_HOME/cn30b-receipt.json}
CN30B_FACTS=$SCN_HOME/cn30b-facts.jsonl
CN30B_SECTIONS=$SCN_HOME/cn30b-sections.txt
export SCN_FAIL_FILE=${SCN_FAIL_FILE:-$SCN_HOME/cn30b-failed.txt}
mkdir -p "$SCN_HOME"
: >"$CN30B_FACTS"
: >"$CN30B_SECTIONS"
: >"$SCN_FAIL_FILE"
DJ_PID=""

# --- receipt -------------------------------------------------------------------------------------

fact() {
  python3 -I -c 'import json, sys; print(json.dumps({"k": sys.argv[1], "v": json.loads(sys.argv[2])}))' \
    "$1" "$2" >>"$CN30B_FACTS"
}
section() {
  printf '%s\t%s\t%s\n' "$1" "$2" "$3" >>"$CN30B_SECTIONS"
  scn_check "$1. $3"
}
cn30b_receipt() {
  mkdir -p "$(dirname "$CN30B_RECEIPT")"
  python3 -I - "$1" "$CN30B_RECEIPT" "$CN30B_FACTS" "$CN30B_SECTIONS" "$SCN_FAIL_FILE" "$SCN_GATEWAY_DIR" \
    "$CN30B_STARTED" "$SCN_ASSERTIONS" "${OMEGA_EXEC_SOURCE_HEAD:-}" <<'PY' || true
import datetime, json, subprocess, sys
status, out, facts_file, sections_file, fail_file, repo, started, assertions, source_head = sys.argv[1:]
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
sections = [l.rstrip("\n").split("\t") for l in open(sections_file, encoding="utf-8") if l.strip()]
failed = open(fail_file, encoding="utf-8").read().strip() or None
passed = status == "0" and failed is None
rows = [{"id": s, "claim": c, "title": t, "result": "pass" if passed or i < len(sections) - 1 else "fail"}
        for i, (s, c, t) in enumerate(sections)]
claims = {}
for row in rows:
    if row["claim"] != "-":
        claims[row["claim"]] = "fail" if row["result"] == "fail" or claims.get(row["claim"]) == "fail" else "pass"
required = ["own_account", "auth_required_before_connect", "no_token_crosses", "other_org", "auth_required_after_revoke", "no_secret_exposed"]
for claim in required:
    claims.setdefault(claim, "not_reached")
receipt = {
    "schema": "cn30b-per-user-oauth-proof/v1",
    "lane": "per-user OAuth",
    "script": "agentcc-gateway/scripts/scenarios/98b_cn_per_user_oauth.sh",
    "result": "pass" if passed and all(claims[c] == "pass" for c in required) else "fail",
    "exit_status": int(status),
    "commit": git("rev-parse", "HEAD"),
    "tree": git("rev-parse", "HEAD^{tree}"),
    "worktree_changes": len([l for l in git("status", "--porcelain").splitlines() if l.strip()]),
    "source_head": source_head or None,
    "started_at": started,
    "finished_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "assertions": int(assertions or 0),
    "failed_assertion": failed,
    "claims": claims,
    "sections": rows,
}
receipt.update(facts)
with open(out, "w", encoding="utf-8") as f:
    json.dump(receipt, f, indent=2)
    f.write("\n")
print("cn30b: receipt %s (%s)" % (out, receipt["result"]))
PY
}
dj_down() { [ -n "$DJ_PID" ] && kill "$DJ_PID" 2>/dev/null; DJ_PID=""; return 0; }
eval "_cn30b_lib_mcp_down() $(declare -f _scn_mcp_down | sed '1d')"
_scn_mcp_down() {
  local status=$?
  if [ -z "${CN30B_RECEIPT_DONE:-}" ]; then
    CN30B_RECEIPT_DONE=1
    cn30b_receipt "$status"
    dj_down
  fi
  _cn30b_lib_mcp_down
}

# --- helpers -------------------------------------------------------------------------------------

INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"cn30b","version":"1"}}}'
LIST='{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
VS=team
keep() {
  local n
  n=$(find "$SCN_DIR/kept" -type f 2>/dev/null | wc -l | tr -d ' ')
  cp "$SCN_DIR/last.body" "$SCN_DIR/kept/$(printf '%05d' "$n")-$1.body" 2>/dev/null
  cp "$SCN_DIR/last.headers" "$SCN_DIR/kept/$(printf '%05d' "$((n + 1))")-$1.headers" 2>/dev/null
  return 0
}
# dj <who> <method> <path> [json]: a console call through Django as one person (session token,
# X-Organization-Id and X-Workspace-Id as the console sends them). Prints the status.
dj() {
  local who=$1 method=$2 path=$3 body=${4:-} tok org ws code
  eval "tok=\${TOK_$who} org=\${ORG_OF_$who} ws=\${WS_OF_$who}"
  local args=(-sS -o "$SCN_DIR/last.body" -D "$SCN_DIR/last.headers" -w '%{http_code}' -X "$method" "$DJ_URL$path"
    -H "Authorization: Bearer $tok" -H "X-Organization-Id: $org" -H "X-Workspace-Id: $ws")
  [ -n "$body" ] && args+=(-H 'Content-Type: application/json' --data-binary "$body")
  code=$(curl "${args[@]}")
  keep "dj-$who"
  echo "$code"
}
wait_for() {
  local name=$1 deadline=$(($(date +%s) + $2))
  shift 2
  until eval "$*"; do
    [ "$(date +%s)" -ge "$deadline" ] &&
      scn_fail "$name" "still false after the wait: $*; last body: $(head -c 800 "$SCN_DIR/last.body" 2>/dev/null)"
    sleep 0.5
  done
}
atof() { eval "printf '%s' \"\${AT_$1}\""; }
# mcp_open <who>: a new MCP session on the org's virtual server with the person's access token, in
# this shell. Sets OPENED ("ok" or "initialize <status>") and SCN_MCP_SESSION.
mcp_open() {
  local code
  SCN_MCP_SESSION=""
  code=$(SCN_KEY=$(atof "$1") scn_mcp_post "/mcp/v/$VS" "$INIT")
  keep "open-$1"
  if [ "$code" != 200 ]; then OPENED="initialize $code"; return 0; fi
  SCN_MCP_SESSION=$(scn_hdr Mcp-Session-Id)
  export SCN_MCP_SESSION
  SCN_KEY=$(atof "$1") scn_mcp_post "/mcp/v/$VS" '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
  OPENED=ok
}
# crm_tools <who>: the crm tool names the person's tools/list shows, comma separated, sorted.
crm_tools() {
  mcp_open "$1"
  [ "$OPENED" = ok ] || { echo "$OPENED"; return 0; }
  SCN_KEY=$(atof "$1") scn_mcp_post "/mcp/v/$VS" "$LIST" >/dev/null
  keep "list-$1"
  unset SCN_MCP_SESSION
  jq -r '[.result.tools[]?.name | select(startswith("crm"))] | sort | join(",")' "$SCN_DIR/last.body" 2>/dev/null
}
# mcp_call <who> <tool>: one tools/call by the person in a new session. Sets CALL_CODE, CALL_ERR
# ("none", "<json-rpc code>" or "isError <code>"), CALL_REASON and CALL_LINK.
mcp_call() {
  local body
  mcp_open "$1"
  if [ "$OPENED" != ok ]; then CALL_CODE=$OPENED CALL_ERR="-" CALL_REASON="" CALL_LINK=""; return 0; fi
  body=$(jq -nc --arg t "$2" '{jsonrpc:"2.0",id:3,method:"tools/call",params:{name:$t,arguments:{text:"cn30b"}}}')
  CALL_CODE=$(SCN_KEY=$(atof "$1") scn_mcp_post "/mcp/v/$VS" "$body")
  keep "call-$1"
  unset SCN_MCP_SESSION
  CALL_ERR=$(jq -r 'if .error then "\(.error.code)" elif .result.isError == true then
    "isError \((.result._meta["io.futureagi/error"] // {}).code // "-")" else "none" end' <"$SCN_DIR/last.body" 2>/dev/null)
  CALL_REASON=$(jq -r '(.result._meta["io.futureagi/error"] // {}).metadata.reason // ""' <"$SCN_DIR/last.body" 2>/dev/null)
  CALL_LINK=$(jq -r '(.result._meta["io.futureagi/error"] // {}).metadata.connect_url // ""' <"$SCN_DIR/last.body" 2>/dev/null)
}
calls() { scn_mcp_received "$1" 0 tools/call | jq '.received | length'; }
# hashes <mock> [method]: the distinct SHA-256s of the Authorization the mock got, sorted, comma separated.
hashes() { scn_mcp_received "$1" 0 "${2:-}" | jq -r '[.received[].auth.sha256 // "none"] | unique | sort | join(",")'; }
last_hash() { scn_mcp_received "$1" 1 tools/call | jq -r '.received[0].auth.sha256 // "none"'; }
token_fetch() { # <org> <user id>: Django's answer to the gateway's request for that person's crm token
  curl -sS -o "$SCN_DIR/last.body" -w '%{http_code}' -X POST "$DJ_URL/agentcc/internal/mcp/upstream-token/" \
    -H "X-Agentcc-MCP-Internal: $DJANGO_SECRET" -H 'Content-Type: application/json' \
    --data-binary "$(jq -nc --arg o "$1" --arg u "$2" '{org_id: $o, user_id: $u, server_id: "crm"}')" >/dev/null
  jq -r '.error // "token"' "$SCN_DIR/last.body"
  : >"$SCN_DIR/last.body" # it may hold a token
}
# connect <who> <mock> <login> [body]: the person's own connect on crm: the start, the vendor's consent
# (login_hint names the vendor account), the callback in the same browser (the start's cookie).
connect() {
  local who=$1 mock=$2 login=$3 body=${4:-'{}'} url cookie loc code
  code=$(dj "$who" POST /agentcc/mcp/connect/crm/start/ "$body")
  scn_assert_eq "$SID.${who}_starts_their_connect" 200 "$code"
  url=$(jq -r '.result.authorize_url' "$SCN_DIR/last.body")
  cookie=$(awk 'tolower($1) == "set-cookie:" && $2 ~ /^__Host-agentcc_mcp_connect_/ { sub(/;.*/, "", $2); print $2 }' \
    "$SCN_DIR/last.headers" | tr -d '\r' | head -1)
  [ -n "$cookie" ] || scn_fail "$SID.${who}_start_cookie" "the start set no __Host- connect cookie"
  code=$(curl -sS -o /dev/null -w '%{http_code}' "$url")
  scn_assert_eq "$SID.${who}_vendor_shows_consent" 200 "$code"
  loc=$(python3 -I - "$url" "$login" <<'PY'
import sys, urllib.parse as u, urllib.request as r
url, login = sys.argv[1:]
parts = u.urlsplit(url)
form = dict(u.parse_qsl(parts.query))
form.update({"decision": "approve", "login_hint": login})
class Stop(r.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None
opener = r.build_opener(Stop)
try:
    opener.open(r.Request(u.urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")), data=u.urlencode(form).encode()))
except r.HTTPError as e:
    print(e.headers.get("Location", ""))
PY
)
  case "$loc" in "$DJ_URL/agentcc/mcp/connect/callback/?"*) ;; *) scn_fail "$SID.${who}_vendor_redirect" "the vendor sent the browser to '${loc%%\?*}'" ;; esac
  code=$(curl -sS -o "$SCN_DIR/last.body" -D "$SCN_DIR/last.headers" -w '%{http_code}' -H "Cookie: $cookie" "$loc")
  keep "callback-$who"
  scn_assert_eq "$SID.${who}_callback_connects" 200 "$code"
  grep -q 'Connected' "$SCN_DIR/last.body" || scn_fail "$SID.${who}_callback_text" "$(head -c 300 "$SCN_DIR/last.body")"
}
# client_sign_in <who>: an MCP client signs the person in through Django's authorization server for
# the org's virtual server (PKCE S256, the person's consent through the console API) and keeps the
# access token in AT_<who>.
REDIRECT=http://127.0.0.1:47123/callback
client_sign_in() {
  local who=$1 pk verifier challenge loc req code at
  pk=$(python3 -I -c 'import base64, hashlib, secrets
v = secrets.token_urlsafe(48)
print(v, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode())')
  verifier=${pk% *} challenge=${pk#* }
  loc=$(curl -sS -o /dev/null -w '%{redirect_url}' -G "$ISS/authorize" --data-urlencode response_type=code \
    --data-urlencode "client_id=$CLIENT_ID" --data-urlencode "redirect_uri=$REDIRECT" \
    --data-urlencode "code_challenge=$challenge" --data-urlencode code_challenge_method=S256 \
    --data-urlencode "resource=$RESOURCE" --data-urlencode scope=mcp:tools --data-urlencode "state=st-$who")
  req=$(python3 -I -c 'import sys, urllib.parse as u; print(u.parse_qs(u.urlsplit(sys.argv[1]).query).get("request", [""])[0])' "$loc")
  [ -n "$req" ] || scn_fail "3.${who}_authorize" "the authorization request did not reach the consent page: '${loc%%\?*}'"
  scn_assert_eq "3.${who}_sees_the_consent" 200 "$(dj "$who" GET "/agentcc/mcp-oauth/consent/$req/")"
  scn_assert_eq "3.${who}_approves" 200 "$(dj "$who" POST "/agentcc/mcp-oauth/consent/$req/" '{"decision":"approve"}')"
  code=$(jq -r '.result.redirect_to' "$SCN_DIR/last.body" | python3 -I -c 'import sys, urllib.parse as u
q = u.parse_qs(u.urlsplit(sys.stdin.read().strip()).query); print(q.get("code", [""])[0])')
  [ -n "$code" ] || scn_fail "3.${who}_code" "the consent gave no code"
  at=$(curl -sS -X POST "$ISS/token" --data-urlencode grant_type=authorization_code --data-urlencode "code=$code" \
    --data-urlencode "redirect_uri=$REDIRECT" --data-urlencode "client_id=$CLIENT_ID" \
    --data-urlencode "code_verifier=$verifier" | jq -r '.access_token // ""')
  [ -n "$at" ] || scn_fail "3.${who}_token" "the code was not exchanged for an access token"
  eval "AT_$who=\$at"
  ALL_AT="$ALL_AT${ALL_AT:+,}$at"
}

# --- 0. the stack --------------------------------------------------------------------------------

w3_need_django
for tool in go curl jq python3; do command -v "$tool" >/dev/null || scn_fail setup "$tool is required"; done
RUN=$(python3 -I -c 'import secrets; print(secrets.token_hex(6))')
# Fake values made for this run only; none is written to a committed file.
APP_A_ID=scn-cn30b-app-a-$RUN
APP_A_SECRET=scn-cn30b-app-a-secret-$RUN-not-a-secret
APP_B_ID=scn-cn30b-app-b-$RUN
APP_B_SECRET=scn-cn30b-app-b-secret-$RUN-not-a-secret
DJANGO_SECRET=scn-cn30b-django-internal-$RUN-not-a-secret-0123456789
WEBHOOK_SECRET=scn-cn30b-webhook-$RUN-not-a-secret
ALL_AT=""

DJ_PORT=$(_scn_free_port)
DJ_URL=http://127.0.0.1:$DJ_PORT
MCPMOCK_OAUTH_CLIENT_SECRET=$APP_A_SECRET scn_mcp_up crma --era 2025-06-18 --auth oauth --oauth-client "$APP_A_ID" \
  --oauth-redirect-uri "$DJ_URL/agentcc/mcp/connect/callback/" --oauth-auth-method client_secret_post \
  --oauth-grant authorization_code --oauth-grant refresh_token
MCPMOCK_OAUTH_CLIENT_SECRET=$APP_B_SECRET scn_mcp_up crmb --era 2025-06-18 --auth oauth --oauth-client "$APP_B_ID" \
  --oauth-redirect-uri "$DJ_URL/agentcc/mcp/connect/callback/" --oauth-auth-method client_secret_post \
  --oauth-grant authorization_code --oauth-grant refresh_token
ISS=$DJ_URL/agentcc/mcp-oauth
mkdir -p "$SCN_BIN"
TEMPLATE=$SCN_BIN/cn30b-gateway.yaml
sed -e "s#@@CN30B_DJANGO_URL@@#$DJ_URL#g" -e "s#@@CN30B_WEBHOOK_SECRET@@#$WEBHOOK_SECRET#" \
  -e "s#@@CN30B_DJANGO_SECRET@@#$DJANGO_SECRET#" -e "s/@@CN30B_CRMA_PORT@@/$SCN_MCP_CRMA_PORT/" \
  -e "s/@@CN30B_CRMB_PORT@@/$SCN_MCP_CRMB_PORT/" "$FIX/gateway.yaml" >"$TEMPLATE"
grep -v '^ *#' "$TEMPLATE" | grep -q '@@CN30B_' && scn_fail setup "unrendered placeholders in $TEMPLATE"
scn_mcp_gateway_template "$TEMPLATE"
scn_up
RESOURCE=$SCN_GW/mcp/v/$VS
mkdir -p "$SCN_DIR/kept"
bs_django_up
env AGENTCC_GATEWAY_INTERNAL_URL="$SCN_GW" AGENTCC_ADMIN_TOKEN="$SCN_ADMIN_TOKEN" AGENTCC_WEBHOOK_SECRET="$WEBHOOK_SECRET" \
  AGENTCC_MCP_DJANGO_SECRET="$DJANGO_SECRET" AGENTCC_MCP_CONNECT_CALLBACK_URL="$DJ_URL/agentcc/mcp/connect/callback/" \
  AGENTCC_MCP_EGRESS_PRIVATE_ALLOW="127.0.0.1:$SCN_MCP_CRMA_PORT,127.0.0.1:$SCN_MCP_CRMB_PORT" \
  AGENTCC_MCP_OAUTH_ISSUER="$ISS" AGENTCC_MCP_OAUTH_RESOURCE_BASES="$SCN_GW/mcp" \
  NO_STARTUP_DB_MUTATIONS=false bash "$GATE_DJANGO_MANAGE" runserver "127.0.0.1:$DJ_PORT" --noreload \
  >"$SCN_DIR/django-server.log" 2>&1 &
DJ_PID=$!
deadline=$(($(date +%s) + 180))
until curl -sS -o /dev/null "$DJ_URL/agentcc/api-keys/bulk/" 2>/dev/null; do
  kill -0 "$DJ_PID" 2>/dev/null || scn_fail setup "Django's dev server exited: $(tail -20 "$SCN_DIR/django-server.log")"
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail setup "Django's dev server did not answer in 180 s"
  sleep 1
done
fact stack "$(jq -nc --arg gw "$SCN_GW" --arg dj "$DJ_URL" '{gateway: ("agentcc built from this tree with MCP sign-in on, " + $gw),
  control_plane: ("Django dev server on Postgres and Redis, " + $dj + "; keys and org configs synced every 2 s; each person token fetched on mcp-upstream-token-v1; the authorization server MCP clients sign people in with"),
  vendors: "one agentcc-mcpmock per org, --auth oauth with the org own OAuth app registered in advance (its secret from the environment)",
  orgs: 2, people_per_org: 2, roles: ["Owner", "Member"], callers: "MCP clients signed in per person (PKCE S256, consent, 15-minute access token for the org virtual server)"}')"
fact deviations '[
  "The vendors are agentcc-mcpmock in OAuth mode on loopback, not real vendors: the run registers its own OAuth app with each mock (the owner registers the real apps; this run calls no real vendor and no futureagi.com host). Django and the gateway reach them through their egress private lists.",
  "The browser is curl: the connect start and the callback carry the start cookie by hand, the vendor consent is posted directly, and the MCP client consent is approved through the console API the consent page calls.",
  "The MCP client is curl with a dynamically registered public client and a loopback redirect, as native MCP clients register."
]'

# --- 1. orgs and people --------------------------------------------------------------------------

section 1 - "two orgs, an Owner and a Member each"
bs_org "CN30b org A $(date -u +%Y-%m-%dT%H:%M:%SZ)"
ORG_A=$BS_ORG
bs_org "CN30b org B $(date -u +%Y-%m-%dT%H:%M:%SZ)"
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
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
def person(org, tag, role, level):
    user = User.objects.create_user(email=f'cn30b-{tag}-{secrets.token_hex(4)}@example.com',
        password=secrets.token_urlsafe(16), name=f'CN30b {tag}', organization=org, organization_role=role)
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
    ws = Workspace.no_workspace_objects.create(name=f'CN30b {tag}', organization=org, is_default=True,
        is_active=True, created_by=owner)
    WorkspaceMembership.no_workspace_objects.create(user=owner, workspace=ws, role='Workspace Owner',
        level=Level.OWNER, organization_membership=owner_m, is_active=True)
    member, member_m = person(org, tag.lower() + '2', OrganizationRoles.MEMBER, Level.MEMBER)
    WorkspaceMembership.no_workspace_objects.create(user=member, workspace=ws,
        role=OrganizationRoles.WORKSPACE_MEMBER, level=Level.WORKSPACE_MEMBER,
        organization_membership=member_m, is_active=True)
    out[tag] = {'ws': str(ws.pk),
        'p1': {'id': str(owner.pk), 'token': token(owner)},
        'p2': {'id': str(member.pk), 'token': token(member)}}
bs_out(out)" "$ORG_A" "$ORG_B"
P=$SCN_DIR/bs-people.json
for o in A B; do
  for n in 1 2; do
    eval "TOK_$o$n=\$(jq -r .$o.p$n.token \"\$P\") UID_$o$n=\$(jq -r .$o.p$n.id \"\$P\")"
    eval "ORG_OF_$o$n=\$ORG_$o WS_OF_$o$n=\$(jq -r .$o.ws \"\$P\")"
  done
done
SESSION_TOKENS=$(jq -c '[.A.p1.token, .A.p2.token, .B.p1.token, .B.p2.token]' "$P")
rm -f "$P" # it holds the session tokens

# --- 2. a per-person OAuth server per org ----------------------------------------------------------

section 2 - "each Owner adds a server that connects each person's own account, and stores the org's own OAuth app"
for o in A B; do
  m=$(echo "crm$o" | tr '[:upper:]' '[:lower:]')
  url=$(eval "printf '%s' \"\${SCN_MCP_$(echo "$m" | tr '[:lower:]' '[:upper:]')_URL}\"")
  body=$(jq -nc --arg u "$url/mcp" '{server_id: "crm", config: {url: $u, transport: "http", credential: {mode: "per_user"}}}')
  scn_assert_eq "2.${o}_owner_adds_crm" 200 "$(dj "${o}1" POST /agentcc/gateways/default/update-mcp-server/ "$body")"
  scn_assert_eq "2.${o}_switches_mcp_on" 200 "$(dj "${o}1" POST /agentcc/gateways/default/update-config/ '{"mcp":{"enabled":true}}')"
  scn_assert_eq "2.${o}_virtual_server" 200 "$(dj "${o}1" POST /agentcc/gateways/default/update-config/ \
    "{\"mcp\":{\"virtual_servers\":{\"$VS\":{\"title\":\"Team\",\"members\":[\"crm\"],\"access\":{\"default_for_org\":true}}}}}")"
  if [ "$o" = A ]; then app_id=$APP_A_ID app_secret=$APP_A_SECRET; else app_id=$APP_B_ID app_secret=$APP_B_SECRET; fi
  app=$(jq -nc --arg i "$app_id" --arg s "$app_secret" '{server_id: "crm", client_id: $i, client_secret: $s}')
  scn_assert_eq "2.${o}_owner_stores_the_app" 201 "$(dj "${o}1" POST /agentcc/connectors/oauth-clients/ "$app")"
done
scn_assert_eq 2.vendor_a_untouched 0 "$(scn_mcp_received crma 0 | jq '.received | length')"

# --- 3. every person signs an MCP client in --------------------------------------------------------

section 3 - "an MCP client signs each person in for their org's virtual server"
CLIENT_ID=$(curl -sS -X POST "$ISS/register" -H 'Content-Type: application/json' --data-binary \
  "$(jq -nc --arg r "$REDIRECT" '{client_name: "CN30b MCP client", redirect_uris: [$r], grant_types: ["authorization_code", "refresh_token"], response_types: ["code"], token_endpoint_auth_method: "none"}')" |
  jq -r '.client_id // ""')
[ -n "$CLIENT_ID" ] || scn_fail 3.register "dynamic registration gave no client_id"
for who in A1 A2 B1 B2; do client_sign_in "$who"; done
for who in A1 A2 B1 B2; do
  sub=$(atof "$who" | cut -d. -f2 | python3 -I -c 'import base64, json, sys
s = sys.stdin.read().strip(); print(json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))["sub"])')
  scn_assert_eq "3.${who}_token_names_the_person" true "$([ "$sub" = "$(eval "printf '%s' \"\${UID_$who}\"")" ] && echo true || echo false)"
done
wait_for 3.a1_reaches_the_virtual_server 60 'mcp_open A1; unset SCN_MCP_SESSION; [ "$OPENED" = ok ]'
scn_assert_eq 3.a1_sees_no_crm_tool_before_anyone_connects "" "$(crm_tools A1)"
scn_assert_eq 3.vendor_a_untouched_before_anyone_connects 0 "$(scn_mcp_received crma 0 | jq '.received | length')"

# --- 4. own account ------------------------------------------------------------------------------

section 4 own_account "a person connects their own account; the server is listed and called through it alone"
SID=4
connect A1 crma a1-account
wait_for 4.a1_sees_the_crm_tools 60 "[ -n \"\$(crm_tools A1)\" ]"
CRM_TOOLS=$(crm_tools A1)
scn_assert_eq 4.crm_echo_listed true "$(echo ",$CRM_TOOLS," | grep -q ',crm__echo,' && echo true || echo false)"
mcp_call A1 crm__echo
scn_assert_eq 4.a1_call_answered "200 none" "$CALL_CODE $CALL_ERR"
H_A1=$(last_hash crma)
scn_assert_eq 4.a1_call_carried_a_token true "$([ "$H_A1" != none ] && echo true || echo false)"
scn_assert_eq 4.vendor_a_saw_only_a1 "$H_A1" "$(hashes crma)"
fact listed_through '{"listing": "the per-person server is listed with the first connected person own account; every vendor request so far carried that account token"}'

# --- 5. auth required before connect -----------------------------------------------------------------

section 5 auth_required_before_connect "a person who has not connected is asked to connect, with a link made for them, and nothing is sent"
SID=5
scn_assert_eq 5.a2_sees_the_tools "$CRM_TOOLS" "$(crm_tools A2)"
calls_before=$(calls crma)
mcp_call A2 crm__echo
scn_assert_eq 5.a2_asked_to_connect "200 isError auth_required" "$CALL_CODE $CALL_ERR"
scn_assert_eq 5.a2_reason "no_token" "$CALL_REASON"
case "$CALL_LINK" in "$DJ_URL/agentcc/mcp/connect/crm/start/?"*) ok=true ;; *) ok=false ;; esac
scn_assert_eq 5.a2_link_is_the_connect_start true "$ok"
A2_LINK=$CALL_LINK
scn_assert_eq 5.vendor_got_nothing_for_a2 "$calls_before" "$(calls crma)"
A2_BINDING=$(python3 -I -c 'import sys, urllib.parse as u; print(u.parse_qs(u.urlsplit(sys.argv[1]).query).get("binding", [""])[0])' "$A2_LINK")
scn_assert_eq 5.a1_cannot_use_a2s_link 403 "$(dj A1 POST /agentcc/mcp/connect/crm/start/ "$(jq -nc --arg b "$A2_BINDING" '{binding: $b}')")"
scn_assert_jq 5.a1_refused_as_not_the_linked_person '.code' binding_mismatch
scn_assert_eq 5.vendor_still_saw_only_a1 "$H_A1" "$(hashes crma)"

# --- 6. no token crosses between people ------------------------------------------------------------

section 6 no_token_crosses "two people of one org each call with their own account and never the other's"
SID=6
connect A2 crma a2-account "$(jq -nc --arg b "$A2_BINDING" '{binding: $b}')"
for round in 1 2; do
  for who in A2 A1; do
    mcp_call "$who" crm__echo
    scn_assert_eq "6.${who}_call_${round}" "200 none" "$CALL_CODE $CALL_ERR"
    eval "HN_$who=\$(last_hash crma)"
  done
  scn_assert_eq "6.a1_keeps_its_own_token_$round" "$H_A1" "$HN_A1"
  [ "$round" = 1 ] && H_A2=$HN_A2
  scn_assert_eq "6.a2_keeps_its_own_token_$round" "$H_A2" "$HN_A2"
done
scn_assert_eq 6.two_accounts_two_tokens true "$([ "$H_A1" != "$H_A2" ] && [ "$H_A2" != none ] && echo true || echo false)"
scn_assert_eq 6.vendor_a_saw_exactly_the_two "$(printf '%s\n%s\n' "$H_A1" "$H_A2" | sort | paste -sd, -)" "$(hashes crma)"

# --- 7. another org --------------------------------------------------------------------------------

section 7 other_org "another org's people use their own vendor and accounts; neither vendor sees the other org"
SID=7
wait_for 7.b1_reaches_the_virtual_server 60 'mcp_open B1; unset SCN_MCP_SESSION; [ "$OPENED" = ok ]'
scn_assert_eq 7.b1_sees_no_crm_tool_from_org_a "" "$(crm_tools B1)"
connect B1 crmb b1-account
wait_for 7.b1_sees_the_crm_tools 60 "[ -n \"\$(crm_tools B1)\" ]"
mcp_call B1 crm__echo
scn_assert_eq 7.b1_call_answered "200 none" "$CALL_CODE $CALL_ERR"
H_B1=$(last_hash crmb)
mcp_call B2 crm__echo
scn_assert_eq 7.b2_asked_to_connect "200 isError auth_required" "$CALL_CODE $CALL_ERR"
scn_assert_eq 7.vendor_b_saw_only_b1 "$H_B1" "$(hashes crmb)"
scn_assert_eq 7.vendor_a_never_saw_org_b "$(printf '%s\n%s\n' "$H_A1" "$H_A2" | sort | paste -sd, -)" "$(hashes crma)"
scn_assert_eq 7.org_b_token_is_its_own true "$([ "$H_B1" != "$H_A1" ] && [ "$H_B1" != "$H_A2" ] && echo true || echo false)"
fact fingerprints "$(jq -nc --arg a1 "${H_A1:0:12}" --arg a2 "${H_A2:0:12}" --arg b1 "${H_B1:0:12}" \
  '{note: "first 12 hex of the SHA-256 of the Authorization each vendor got", a1: $a1, a2: $a2, b1: $b1}')"

# --- 8. auth required after revoke -----------------------------------------------------------------

section 8 auth_required_after_revoke "a person's disconnect revokes their account: they are asked to connect again and nothing more is sent for them"
SID=8
scn_assert_eq 8.a1_disconnects 200 "$(dj A1 DELETE /agentcc/mcp/connect/crm/)"
calls_before=$(calls crma)
mcp_call A1 crm__echo
scn_assert_eq 8.a1_asked_to_connect_again "200 isError auth_required" "$CALL_CODE $CALL_ERR"
fact revoked_reason "$(jq -nc --arg r "$CALL_REASON" '$r')"
scn_assert_eq 8.vendor_got_nothing_more_for_a1 "$calls_before" "$(calls crma)"
scn_assert_eq 8.a1_token_fetch_refused true "$([ "$(token_fetch "$ORG_A" "$UID_A1")" != token ] && echo true || echo false)"
mcp_call A2 crm__echo
scn_assert_eq 8.a2_still_answered "200 none" "$CALL_CODE $CALL_ERR"
scn_assert_eq 8.a2_still_its_own_token "$H_A2" "$(last_hash crma)"
scn_assert_eq 8.a2_token_fetch_served token "$(token_fetch "$ORG_A" "$UID_A2")"
scn_assert_eq 8.a1_has_no_connection_left 404 "$(dj A1 DELETE /agentcc/mcp/connect/crm/)"

# --- 9. no secret ----------------------------------------------------------------------------------

section 9 no_secret_exposed "no vendor token, refresh token, access token, app secret or internal secret in a log, response or table"
SECRETS_JSON=$(jq -nc --arg a "$APP_A_SECRET" --arg b "$APP_B_SECRET" --arg d "$DJANGO_SECRET" --arg at "$ALL_AT" \
  '{app_a_secret: $a, app_b_secret: $b, django_internal_secret: $d, vendor_access_token: "mcpmock-at-", vendor_refresh_token: "mcpmock-rt-"}
   + ([$at | split(",")[] | select(length > 0)] | to_entries | map({key: "mcp_access_token_\(.key)", value: .value}) | from_entries)')
python3 -I - "$SECRETS_JSON" "$SCN_DIR" "$SCN_MCP_DIR" <<'PY' >"$SCN_DIR/scan.json" || scn_fail 9.scan "the file scan failed"
import json, os, sys
secrets, *roots = sys.argv[1:]
secrets = json.loads(secrets)
hits, files = [], 0
for root in roots:
    for base, _, names in os.walk(root):
        for name in names:
            path = os.path.join(base, name)
            if name.endswith((".yaml", ".bin")) or "/bin/" in path:
                continue  # the rendered config holds the internal secret by design
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            files += 1
            for label, value in secrets.items():
                if value in text:
                    hits.append({"file": os.path.relpath(path, root), "secret": label})
print(json.dumps({"files": files, "hits": hits}))
PY
scn_assert_jq 9.no_secret_in_any_file '.hits | map("\(.secret) in \(.file)") | join("; ")' "" "$SCN_DIR/scan.json"
scn_assert_jq 9.files_scanned '.files > 10' true "$SCN_DIR/scan.json"
bs_django_py db_scan "
import json
from django.db import connection
secrets = json.loads(BS_ARGS[0])
hits, tables, control_found = [], 0, False
with connection.cursor() as c:
    c.execute(\"SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'\")
    for (name,) in c.fetchall():
        q = connection.ops.quote_name(name)
        tables += 1
        c.execute(f'SELECT count(*) FROM {q} t WHERE strpos(t::text, %s) > 0', [BS_ARGS[1]])
        if c.fetchone()[0]:
            control_found = True
        for label, value in secrets.items():
            c.execute(f'SELECT count(*) FROM {q} t WHERE strpos(t::text, %s) > 0', [value])
            if c.fetchone()[0]:
                hits.append({'table': name, 'secret': label})
from agentcc.models.mcp_credential import AgentccMCPCredential as C
rows = C.objects.filter(organization_id__in=BS_ARGS[2:4], kind='oauth', scope=C.Scope.USER)
bs_out({'tables': tables, 'hits': hits, 'control_found': control_found,
        'encrypted': all(r.encrypted_secret.startswith('enc::') for r in rows), 'rows': rows.count()})" \
  "$SECRETS_JSON" "CN30b org A" "$ORG_A" "$ORG_B"
D=$SCN_DIR/bs-db_scan.json
scn_assert_jq 9.db_scan_control_sees_a_value '.control_found' true "$D"
scn_assert_jq 9.no_secret_in_any_table '.hits | map("\(.secret) in \(.table)") | join("; ")' "" "$D"
scn_assert_jq 9.vault_rows_are_ciphertext '.encrypted and .rows >= 2' true "$D"
fact credential_scan "$(jq -c --slurpfile db "$D" '{files_scanned: .files, file_hits: (.hits | length),
  tables_scanned: $db[0].tables, table_hits: ($db[0].hits | length),
  values: ["both orgs OAuth app secrets", "the gateway internal secret", "every MCP access token issued in the run", "every vendor access and refresh token (by their prefixes)"]}' \
  "$SCN_DIR/scan.json")"
: "$SESSION_TOKENS"
scn_pass
