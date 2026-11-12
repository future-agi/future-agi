#!/usr/bin/env bash
# The organization's one shared account, signed in over
# OAuth through the organization's own OAuth app, end to end on the stack: the real gateway binary built
# from this tree; Django's dev server as its control plane (keys and org configs synced every 2 s, the
# gateway's token fetch on mcp-upstream-token-v1), on Postgres and Redis; one agentcc-mcpmock per org in
# OAuth mode as the vendor (an MCP server and its own authorization server), which takes only a client
# registered with it in advance. Two orgs, an Owner and a Member each. It proves:
#
#   own app       a shared sign-in is refused until the org stores its own OAuth app (the vendor takes
#                 pre-registered clients only); an Owner stores it through the organization OAuth client
#                 routes, and the sign-in then identifies itself as that client and completes only
#                 because it proves that client's secret at the vendor's token endpoint.
#   admin only    a Member can neither store the app, read it, start the shared sign-in nor end it.
#   per call      after the Owner signs in, the gateway connects the server and every call by every key
#                 of the org (agent mode: org keys, no person) carries that one account's token, fetched
#                 just in time; the audit names no person.
#   isolation     each org's calls carry only its own account's token, and neither vendor ever sees the
#                 other org's (the mocks keep SHA-256 only).
#   refresh       a token close to its expiry is refreshed in place (with the app's secret) and the next
#                 call carries the new one.
#   disconnect    the Owner's disconnect revokes the account: the server is no longer connected, its
#                 tools are gone, the token fetch answers revoked and the vendor gets nothing more.
#   no secret     no vendor token, refresh token, app secret or the gateway's internal secret is in any
#                 log, response or table of the database (the vault holds ciphertext).
#
# It writes a receipt JSON naming the commit (CN24_RECEIPT, default $SCN_HOME/cn24-receipt.json), on a red
# too, with the failed assertion.
#
# The "a" keeps it out of run_all.sh's [0-9][0-9]_*.sh glob (and connectors/run_all.sh's): like 97a it
# needs Django, Postgres and Redis.
#
#   SCN_REDIS=<host:port> SCN_REDIS_DB=<n> GATE_DJANGO_MANAGE=<manage.py wrapper> [CN24_RECEIPT=<path>] \
#     bash scripts/scenarios/98a_cn_shared_oauth.sh
#
# The wrapper is a gate's manage.py wrapper (its own Postgres database and Redis DB, as
# gates/lib/django_live.sh describes). Tools: go, curl, jq, python3.
set -u
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
export SCN_LANE=${SCN_LANE:-cn24-shared-oauth}
# shellcheck source=gates/lib/django_live.sh
source "$SCRIPT_DIR/gates/lib/django_live.sh"
# shellcheck source=lib_mcp.sh
source "$SCRIPT_DIR/lib_mcp.sh"

FIX=$SCN_ROOT/fixtures/CN24
CN24_STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
CN24_RECEIPT=${CN24_RECEIPT:-$SCN_HOME/cn24-receipt.json}
CN24_FACTS=$SCN_HOME/cn24-facts.jsonl
CN24_SECTIONS=$SCN_HOME/cn24-sections.txt
export SCN_FAIL_FILE=${SCN_FAIL_FILE:-$SCN_HOME/cn24-failed.txt}
mkdir -p "$SCN_HOME"
: >"$CN24_FACTS"
: >"$CN24_SECTIONS"
: >"$SCN_FAIL_FILE"
DJ_PID=""

# --- receipt -------------------------------------------------------------------------------------

fact() {
  python3 -I -c 'import json, sys; print(json.dumps({"k": sys.argv[1], "v": json.loads(sys.argv[2])}))' \
    "$1" "$2" >>"$CN24_FACTS"
}
section() {
  printf '%s\t%s\t%s\n' "$1" "$2" "$3" >>"$CN24_SECTIONS"
  scn_check "$1. $3"
}
cn24_receipt() {
  mkdir -p "$(dirname "$CN24_RECEIPT")"
  python3 -I - "$1" "$CN24_RECEIPT" "$CN24_FACTS" "$CN24_SECTIONS" "$SCN_FAIL_FILE" "$SCN_GATEWAY_DIR" \
    "$CN24_STARTED" "$SCN_ASSERTIONS" "${OMEGA_EXEC_SOURCE_HEAD:-}" <<'PY' || true
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
required = ["own_app", "admin_only", "per_call", "isolation", "refresh", "disconnect", "no_secret_exposed"]
for claim in required:
    claims.setdefault(claim, "not_reached")
receipt = {
    "schema": "cn24-shared-oauth-proof/v1",
    "lane": "shared-account OAuth",
    "script": "agentcc-gateway/scripts/scenarios/98a_cn_shared_oauth.sh",
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
print("cn24: receipt %s (%s)" % (out, receipt["result"]))
PY
}
dj_down() { [ -n "$DJ_PID" ] && kill "$DJ_PID" 2>/dev/null; DJ_PID=""; return 0; }
eval "_cn24_lib_mcp_down() $(declare -f _scn_mcp_down | sed '1d')"
_scn_mcp_down() {
  local status=$?
  if [ -z "${CN24_RECEIPT_DONE:-}" ]; then
    CN24_RECEIPT_DONE=1
    cn24_receipt "$status"
    dj_down
  fi
  _cn24_lib_mcp_down
}

# --- helpers -------------------------------------------------------------------------------------

INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"cn24","version":"1"}}}'
LIST='{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
keep() {
  local n
  n=$(find "$SCN_DIR/kept" -type f 2>/dev/null | wc -l | tr -d ' ')
  cp "$SCN_DIR/last.body" "$SCN_DIR/kept/$(printf '%05d' "$n")-$1.body" 2>/dev/null
  cp "$SCN_DIR/last.headers" "$SCN_DIR/kept/$(printf '%05d' "$((n + 1))")-$1.headers" 2>/dev/null
  return 0
}
keyof() { eval "printf '%s' \"\${KEY_$1}\""; }
orgof() { eval "printf '%s' \"\${ORG_OF_$1}\""; }
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
# crm_status <org>: "<healthy> <tool count>" of the org's crm server in the gateway's pool status.
crm_status() {
  scn_admin GET "/-/mcp/status?org_id=$1" >/dev/null
  keep "status"
  jq -r '[.servers[]? | select(.server_id == "crm")][0] | "\(.healthy // false) \(.tool_count // 0)"' \
    "$SCN_DIR/last.body" 2>/dev/null
}
crm_error() {
  scn_admin GET "/-/mcp/status?org_id=$1" >/dev/null
  jq -r '[.servers[]? | select(.server_id == "crm")][0].last_error // ""' "$SCN_DIR/last.body" 2>/dev/null
}
# mcp_call <who> <tool>: one tools/call by the person's org key in a new session. Sets CALL_CODE and
# CALL_ERR ("none", "<json-rpc code>" or "isError <code>").
mcp_call() {
  local key body
  key=$(keyof "$1")
  SCN_MCP_SESSION=""
  CALL_CODE=$(SCN_KEY=$key scn_mcp_post /mcp "$INIT")
  keep "open-$1"
  if [ "$CALL_CODE" != 200 ]; then CALL_ERR="initialize $CALL_CODE"; return 0; fi
  SCN_MCP_SESSION=$(scn_hdr Mcp-Session-Id)
  export SCN_MCP_SESSION
  SCN_KEY=$key scn_mcp_post /mcp '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
  body=$(jq -nc --arg t "$2" '{jsonrpc:"2.0",id:3,method:"tools/call",params:{name:$t,arguments:{text:"cn24"}}}')
  CALL_CODE=$(SCN_KEY=$key scn_mcp_post /mcp "$body")
  keep "call-$1"
  CALL_ERR=$(jq -r 'if .error then "\(.error.code)" elif .result.isError == true then
    "isError \((.result._meta["io.futureagi/error"] // {}).code // "-")" else "none" end' <"$SCN_DIR/last.body" 2>/dev/null)
  unset SCN_MCP_SESSION
}
received() { scn_mcp_received "$1" 0 "${2:-}" | jq '.received | length'; }
# hashes <mock>: the distinct SHA-256s of the Authorization the mock got, one per line.
hashes() { scn_mcp_received "$1" 0 | jq -r '[.received[].auth.sha256 // "none"] | unique | .[]'; }
last_hash() { scn_mcp_received "$1" 1 tools/call | jq -r '.received[0].auth.sha256 // "none"'; }
token_fetch() { # <org>: Django's answer to the gateway's shared request for the org's crm
  curl -sS -o "$SCN_DIR/last.body" -w '%{http_code}' -X POST "$DJ_URL/agentcc/internal/mcp/upstream-token/" \
    -H "X-Agentcc-MCP-Internal: $DJANGO_SECRET" -H 'Content-Type: application/json' \
    --data-binary "$(jq -nc --arg o "$1" '{org_id: $o, server_id: "crm", scope: "shared"}')" >/dev/null
  jq -r '.error // "token"' "$SCN_DIR/last.body"
  : >"$SCN_DIR/last.body" # it may hold a token
}
# sign_in <owner> <mock> <login>: the Owner's whole shared sign-in on crm: the start, the vendor's
# consent (login_hint names the vendor account), the callback in the same browser (the start's cookie).
sign_in() {
  local who=$1 mock=$2 login=$3 url cookie loc code
  code=$(dj "$who" POST /agentcc/mcp/connect/crm/start/ '{"mode":"shared"}')
  scn_assert_eq "$SID.${who}_starts_the_shared_sign_in" 200 "$code"
  url=$(jq -r '.result.authorize_url' "$SCN_DIR/last.body")
  SIGNIN_CLIENT=$(python3 -I -c 'import sys, urllib.parse as u; print(u.parse_qs(u.urlsplit(sys.argv[1]).query)["client_id"][0])' "$url")
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

# --- 0. the stack --------------------------------------------------------------------------------

w3_need_django
for tool in go curl jq python3; do command -v "$tool" >/dev/null || scn_fail setup "$tool is required"; done
RUN=$(python3 -I -c 'import secrets; print(secrets.token_hex(6))')
# Fake values made for this run only; none is written to a committed file.
APP_A_ID=scn-cn24-app-a-$RUN
APP_A_SECRET=scn-cn24-app-a-secret-$RUN-not-a-secret
APP_B_ID=scn-cn24-app-b-$RUN
APP_B_SECRET=scn-cn24-app-b-secret-$RUN-not-a-secret
DJANGO_SECRET=scn-cn24-django-internal-$RUN-not-a-secret-0123456789
WEBHOOK_SECRET=scn-cn24-webhook-$RUN-not-a-secret

DJ_PORT=$(_scn_free_port)
DJ_URL=http://127.0.0.1:$DJ_PORT
MCPMOCK_OAUTH_CLIENT_SECRET=$APP_A_SECRET scn_mcp_up crma --era 2025-06-18 --auth oauth --oauth-client "$APP_A_ID" \
  --oauth-redirect-uri "$DJ_URL/agentcc/mcp/connect/callback/" --oauth-auth-method client_secret_post \
  --oauth-grant authorization_code --oauth-grant refresh_token
MCPMOCK_OAUTH_CLIENT_SECRET=$APP_B_SECRET scn_mcp_up crmb --era 2025-06-18 --auth oauth --oauth-client "$APP_B_ID" \
  --oauth-redirect-uri "$DJ_URL/agentcc/mcp/connect/callback/" --oauth-auth-method client_secret_post \
  --oauth-grant authorization_code --oauth-grant refresh_token
mkdir -p "$SCN_BIN"
TEMPLATE=$SCN_BIN/cn24-gateway.yaml
sed -e "s#@@CN24_DJANGO_URL@@#$DJ_URL#g" -e "s#@@CN24_WEBHOOK_SECRET@@#$WEBHOOK_SECRET#" \
  -e "s#@@CN24_DJANGO_SECRET@@#$DJANGO_SECRET#" -e "s/@@CN24_CRMA_PORT@@/$SCN_MCP_CRMA_PORT/" \
  -e "s/@@CN24_CRMB_PORT@@/$SCN_MCP_CRMB_PORT/" "$FIX/gateway.yaml" >"$TEMPLATE"
grep -v '^ *#' "$TEMPLATE" | grep -q '@@CN24_' && scn_fail setup "unrendered placeholders in $TEMPLATE"
scn_mcp_gateway_template "$TEMPLATE"
scn_up
mkdir -p "$SCN_DIR/kept"
bs_django_up
env AGENTCC_GATEWAY_INTERNAL_URL="$SCN_GW" AGENTCC_ADMIN_TOKEN="$SCN_ADMIN_TOKEN" AGENTCC_WEBHOOK_SECRET="$WEBHOOK_SECRET" \
  AGENTCC_MCP_DJANGO_SECRET="$DJANGO_SECRET" AGENTCC_MCP_CONNECT_CALLBACK_URL="$DJ_URL/agentcc/mcp/connect/callback/" \
  AGENTCC_MCP_EGRESS_PRIVATE_ALLOW="127.0.0.1:$SCN_MCP_CRMA_PORT,127.0.0.1:$SCN_MCP_CRMB_PORT" \
  NO_STARTUP_DB_MUTATIONS=false bash "$GATE_DJANGO_MANAGE" runserver "127.0.0.1:$DJ_PORT" --noreload \
  >"$SCN_DIR/django-server.log" 2>&1 &
DJ_PID=$!
deadline=$(($(date +%s) + 180))
until curl -sS -o /dev/null "$DJ_URL/agentcc/api-keys/bulk/" 2>/dev/null; do
  kill -0 "$DJ_PID" 2>/dev/null || scn_fail setup "Django's dev server exited: $(tail -20 "$SCN_DIR/django-server.log")"
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail setup "Django's dev server did not answer in 180 s"
  sleep 1
done
fact stack "$(jq -nc --arg gw "$SCN_GW" --arg dj "$DJ_URL" '{gateway: ("agentcc built from this tree, " + $gw),
  control_plane: ("Django dev server on Postgres and Redis, " + $dj + "; keys and org configs synced every 2 s; the token fetch on mcp-upstream-token-v1"),
  vendors: "one agentcc-mcpmock per org, --auth oauth with one client registered in advance (its secret from the environment)",
  orgs: 2, people_per_org: 2, roles: ["Owner", "Member"]}')"
fact deviations '[
  "The vendors are agentcc-mcpmock in OAuth mode on loopback, not real vendors: the run registers its own OAuth app with each mock (the owner registers the real apps; this run calls no real vendor and no futureagi.com host). Django and the gateway reach them through their egress private lists.",
  "The browser is curl: the shared sign-in start and the callback carry the start cookie by hand, and the vendor consent is posted directly.",
  "The token refresh is brought forward by setting the row expiry 30 s out in the database and purging the gateway cache, instead of waiting out the vendor 15-minute token.",
  "Gateway keys are minted by the org Owner for both people; on a shared server every org key acts with the one signed-in account (agent mode)."
]'

# --- 1. orgs, people and keys --------------------------------------------------------------------

section 1 - "two orgs, an Owner and a Member each; the Owner mints an org key per person"
bs_org "CN24 org A $(date -u +%Y-%m-%dT%H:%M:%SZ)"
ORG_A=$BS_ORG
bs_org "CN24 org B $(date -u +%Y-%m-%dT%H:%M:%SZ)"
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
    user = User.objects.create_user(email=f'cn24-{tag}-{secrets.token_hex(4)}@example.com',
        password=secrets.token_urlsafe(16), name=f'CN24 {tag}', organization=org, organization_role=role)
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
    ws = Workspace.no_workspace_objects.create(name=f'CN24 {tag}', organization=org, is_default=True,
        is_active=True, created_by=owner)
    WorkspaceMembership.no_workspace_objects.create(user=owner, workspace=ws, role='Workspace Owner',
        level=Level.OWNER, organization_membership=owner_m, is_active=True)
    member, member_m = person(org, tag.lower() + '2', OrganizationRoles.MEMBER, Level.MEMBER)
    WorkspaceMembership.no_workspace_objects.create(user=member, workspace=ws,
        role=OrganizationRoles.WORKSPACE_MEMBER, level=Level.WORKSPACE_MEMBER,
        organization_membership=member_m, is_active=True)
    out[tag] = {'ws': str(ws.pk),
        'p1': {'email': owner.email, 'token': token(owner)},
        'p2': {'email': member.email, 'token': token(member)}}
bs_out(out)" "$ORG_A" "$ORG_B"
P=$SCN_DIR/bs-people.json
for o in A B; do
  for n in 1 2; do
    eval "TOK_$o$n=\$(jq -r .$o.p$n.token \"\$P\") EMAIL_$o$n=\$(jq -r .$o.p$n.email \"\$P\")"
    eval "ORG_OF_$o$n=\$ORG_$o WS_OF_$o$n=\$(jq -r .$o.ws \"\$P\")"
  done
done
SESSION_TOKENS=$(jq -c '[.A.p1.token, .A.p2.token, .B.p1.token, .B.p2.token]' "$P")
rm -f "$P" # it holds the session tokens
mint() { # <owner> <for whom>: sets KEY_<whom> and KID_<whom>
  local email code
  eval "email=\${EMAIL_$2}"
  code=$(curl -sS -o "$SCN_DIR/mint.json" -w '%{http_code}' -X POST "$DJ_URL/agentcc/api-keys/" \
    -H "Authorization: Bearer $(eval "printf '%s' \"\${TOK_$1}\"")" -H "X-Organization-Id: $(orgof "$1")" \
    -H "X-Workspace-Id: $(eval "printf '%s' \"\${WS_OF_$1}\"")" -H 'Content-Type: application/json' \
    --data-binary "$(jq -nc --arg n "cn24-$(echo "$2" | tr '[:upper:]' '[:lower:]')" --arg e "$email" '{name: $n, owner: $e}')")
  scn_assert_eq "1.$1_mints_for_$2" 201 "$code"
  eval "KEY_$2=\$(jq -r .result.key \"\$SCN_DIR/mint.json\") KID_$2=\$(jq -r .result.gateway_key_id \"\$SCN_DIR/mint.json\")"
  rm -f "$SCN_DIR/mint.json"
}
mint A1 A1
mint A1 A2
mint B1 B1
mint B1 B2
for who in A1 A2 B1 B2; do
  kid=$(eval "printf '%s' \"\${KID_$who}\"")
  wait_for "1.gateway_holds_key_$who" 30 "[ \"\$(scn_admin GET /-/keys/$kid)\" = 200 ]"
done

# --- 2. a shared OAuth server per org --------------------------------------------------------------

section 2 - "each Owner adds a shared server whose one account signs in over OAuth; it waits for the sign-in"
for o in A B; do
  m=$(echo "crm$o" | tr '[:upper:]' '[:lower:]')
  url=$(eval "printf '%s' \"\${SCN_MCP_$(echo "$m" | tr '[:lower:]' '[:upper:]')_URL}\"")
  body=$(jq -nc --arg u "$url/mcp" '{server_id: "crm", config: {url: $u, transport: "http", credential: {mode: "shared", kind: "oauth"}}}')
  scn_assert_eq "2.${o}_member_cannot_add" 403 "$(dj "${o}2" POST /agentcc/gateways/default/update-mcp-server/ "$body")"
  scn_assert_eq "2.${o}_owner_adds_crm" 200 "$(dj "${o}1" POST /agentcc/gateways/default/update-mcp-server/ "$body")"
  scn_assert_eq "2.${o}_switches_mcp_on" 200 "$(dj "${o}1" POST /agentcc/gateways/default/update-config/ '{"mcp":{"enabled":true}}')"
  scn_assert_eq "2.${o}_default_virtual_server" 200 "$(dj "${o}1" POST /agentcc/gateways/default/update-config/ \
    '{"mcp":{"virtual_servers":{"team":{"title":"Team","members":["crm"],"access":{"default_for_org":true}}}}}')"
done
wait_for 2.a_crm_waits_for_its_account 30 "[ \"\$(crm_status $ORG_A)\" = 'false 0' ]"
scn_assert_eq 2.a_status_says_not_signed_in true "$(crm_error "$ORG_A" | grep -q 'not signed in' && echo true || echo false)"
scn_assert_eq 2.a_vendor_untouched 0 "$(received crma)"
mcp_call A1 crm__echo
scn_assert_eq 2.a_no_tool_before_the_sign_in "200 -32602" "$CALL_CODE $CALL_ERR"

# --- 3. the org's own OAuth app --------------------------------------------------------------------

section 3 own_app "a sign-in is refused until the org stores its own OAuth app; then it signs in as that app"
SID=3
scn_assert_eq 3.a_refused_without_the_app 409 "$(dj A1 POST /agentcc/mcp/connect/crm/start/ '{"mode":"shared"}')"
scn_assert_jq 3.a_refused_code '.code' oauth_client_unavailable
APP_A=$(jq -nc --arg i "$APP_A_ID" --arg s "$APP_A_SECRET" '{server_id: "crm", client_id: $i, client_secret: $s}')
scn_assert_eq 3.a_owner_stores_the_app 201 "$(dj A1 POST /agentcc/connectors/oauth-clients/ "$APP_A")"
scn_assert_eq 3.a_app_secret_never_returned false "$(grep -qF "$APP_A_SECRET" "$SCN_DIR/last.body" && echo true || echo false)"
sign_in A1 crma org-a-account
scn_assert_eq 3.a_signed_in_as_the_orgs_app "$APP_A_ID" "$SIGNIN_CLIENT"
APP_B=$(jq -nc --arg i "$APP_B_ID" --arg s "$APP_B_SECRET" '{server_id: "crm", client_id: $i, client_secret: $s}')
scn_assert_eq 3.b_owner_stores_the_app 201 "$(dj B1 POST /agentcc/connectors/oauth-clients/ "$APP_B")"
sign_in B1 crmb org-b-account
scn_assert_eq 3.b_signed_in_as_the_orgs_app "$APP_B_ID" "$SIGNIN_CLIENT"
fact own_app '{"vendor_takes": "pre-registered clients only (no metadata document, no registration)", "stored_through": "POST /agentcc/connectors/oauth-clients/ (server_id crm)", "proved_at": "the mock token endpoint refuses the client without its secret (401 invalid_client)"}'

# --- 4. admin only ---------------------------------------------------------------------------------

section 4 admin_only "a Member can neither store, read, start nor end the organization's sign-in"
scn_assert_eq 4.a_member_cannot_store_an_app 403 "$(dj A2 POST /agentcc/connectors/oauth-clients/ "$APP_A")"
scn_assert_eq 4.a_member_cannot_read_the_apps 403 "$(dj A2 GET /agentcc/connectors/oauth-clients/)"
scn_assert_eq 4.a_member_cannot_start 403 "$(dj A2 POST /agentcc/mcp/connect/crm/start/ '{"mode":"shared"}')"
scn_assert_jq 4.a_member_start_code '.code' not_admin
scn_assert_eq 4.a_member_cannot_end 403 "$(dj A2 DELETE '/agentcc/mcp/connect/crm/?mode=shared')"
scn_assert_eq 4.a_person_sign_in_never_fills_the_org_slot 400 "$(dj A2 POST /agentcc/mcp/connect/crm/start/ '{}')"

# --- 5. per call -----------------------------------------------------------------------------------

section 5 per_call "every org key's call carries the one signed-in account's token, fetched just in time"
wait_for 5.a_crm_connects 60 "[ \"\$(crm_status $ORG_A)\" = 'true 3' ]"
wait_for 5.b_crm_connects 60 "[ \"\$(crm_status $ORG_B)\" = 'true 3' ]"
for who in A1 A2; do
  mcp_call "$who" crm__echo
  scn_assert_eq "5.${who}_call_answered" "200 none" "$CALL_CODE $CALL_ERR"
  eval "H_$who=\$(last_hash crma)"
done
scn_assert_eq 5.a_keys_share_the_one_account "$H_A1" "$H_A2"
scn_assert_eq 5.a_call_carried_a_token true "$([ "$H_A1" != none ] && echo true || echo false)"
for who in B1 B2; do
  mcp_call "$who" crm__echo
  scn_assert_eq "5.${who}_call_answered" "200 none" "$CALL_CODE $CALL_ERR"
  eval "H_$who=\$(last_hash crmb)"
done
scn_assert_eq 5.b_keys_share_the_one_account "$H_B1" "$H_B2"
bs_django_py audit "
from agentcc.models.mcp_upstream_oauth import AgentccMCPUpstreamTokenAudit as A
rows = A.objects.filter(org_id__in=BS_ARGS, server_id='crm')
bs_out({'rows': rows.count(), 'with_person': rows.exclude(user_id='').count(),
        'served': rows.filter(outcome__in=['ok', 'refreshed']).count()})" "$ORG_A" "$ORG_B"
scn_assert_jq 5.token_fetches_name_no_person '.with_person' 0 "$SCN_DIR/bs-audit.json"
scn_assert_jq 5.token_fetches_served '.served > 0' true "$SCN_DIR/bs-audit.json"

# --- 6. isolation ----------------------------------------------------------------------------------

section 6 isolation "each vendor sees only its own org's account token"
scn_assert_eq 6.orgs_have_different_accounts true "$([ "$H_A1" != "$H_B1" ] && echo true || echo false)"
scn_assert_eq 6.vendor_a_saw_only_org_a "$H_A1" "$(hashes crma | paste -sd, -)"
scn_assert_eq 6.vendor_b_saw_only_org_b "$H_B1" "$(hashes crmb | paste -sd, -)"

# --- 7. refresh ------------------------------------------------------------------------------------

section 7 refresh "a token close to its expiry is refreshed in place, and the next call carries the new one"
bs_django_py expire "
from datetime import timedelta
from django.utils import timezone
from agentcc.models.mcp_credential import AgentccMCPCredential as C
row = C.objects.get(organization_id=BS_ARGS[0], server_id='crm', scope='shared', kind='oauth', status='active')
C.objects.filter(pk=row.pk).update(token_expires_at=timezone.now() + timedelta(seconds=30))
bs_out({'version': row.version})" "$ORG_A"
V_BEFORE=$(jq -r .version "$SCN_DIR/bs-expire.json")
scn_assert_eq 7.gateway_drops_its_copy 200 "$(scn_admin POST /-/mcp/credentials/purge "$(jq -nc --arg o "$ORG_A" '{org_id: $o, server_id: "crm"}')")"
mcp_call A1 crm__echo
scn_assert_eq 7.a_call_after_the_window "200 none" "$CALL_CODE $CALL_ERR"
H_A_NEW=$(last_hash crma)
scn_assert_eq 7.a_call_carries_a_new_token true "$([ "$H_A_NEW" != "$H_A1" ] && [ "$H_A_NEW" != none ] && echo true || echo false)"
bs_django_py refreshed "
from agentcc.models.mcp_credential import AgentccMCPCredential as C
row = C.objects.get(organization_id=BS_ARGS[0], server_id='crm', scope='shared', kind='oauth', status='active')
bs_out({'version': row.version})" "$ORG_A"
scn_assert_jq 7.a_row_rotated_in_place '.version' "$((V_BEFORE + 1))" "$SCN_DIR/bs-refreshed.json"
scn_assert_eq 7.vendor_b_still_only_org_b "$H_B1" "$(hashes crmb | paste -sd, -)"

# --- 8. disconnect ---------------------------------------------------------------------------------

section 8 disconnect "the Owner's disconnect revokes the account: no tools, a revoked token fetch, no vendor call"
scn_assert_eq 8.a_owner_disconnects 200 "$(dj A1 DELETE '/agentcc/mcp/connect/crm/?mode=shared')"
wait_for 8.a_crm_disconnected 60 "[ \"\$(crm_status $ORG_A)\" = 'false 0' ]"
calls_before=$(received crma tools/call)
mcp_call A1 crm__echo
scn_assert_eq 8.a_no_tool_after_the_disconnect "200 -32602" "$CALL_CODE $CALL_ERR"
scn_assert_eq 8.a_vendor_got_nothing_more "$calls_before" "$(received crma tools/call)"
scn_assert_eq 8.a_token_fetch_answers_revoked revoked "$(token_fetch "$ORG_A")"
scn_assert_eq 8.b_still_signed_in token "$(token_fetch "$ORG_B")"
mcp_call B1 crm__echo
scn_assert_eq 8.b_still_answered "200 none" "$CALL_CODE $CALL_ERR"

# --- 9. no secret ----------------------------------------------------------------------------------

section 9 no_secret_exposed "no vendor token, refresh token, app secret or internal secret in a log, response or table"
SECRETS_JSON=$(jq -nc --arg a "$APP_A_SECRET" --arg b "$APP_B_SECRET" --arg d "$DJANGO_SECRET" \
  '{app_a_secret: $a, app_b_secret: $b, django_internal_secret: $d, vendor_access_token: "mcpmock-at-", vendor_refresh_token: "mcpmock-rt-"}')
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
rows = C.objects.filter(organization_id__in=BS_ARGS[2:4], kind='oauth', scope='shared')
bs_out({'tables': tables, 'hits': hits, 'control_found': control_found,
        'encrypted': all(r.encrypted_secret.startswith('enc::') for r in rows), 'rows': rows.count()})" \
  "$SECRETS_JSON" "CN24 org A" "$ORG_A" "$ORG_B"
D=$SCN_DIR/bs-db_scan.json
scn_assert_jq 9.db_scan_control_sees_a_value '.control_found' true "$D"
scn_assert_jq 9.no_secret_in_any_table '.hits | map("\(.secret) in \(.table)") | join("; ")' "" "$D"
scn_assert_jq 9.vault_rows_are_ciphertext '.encrypted and .rows == 2' true "$D"
fact credential_scan "$(jq -c --slurpfile db "$D" '{files_scanned: .files, file_hits: (.hits | length),
  tables_scanned: $db[0].tables, table_hits: ($db[0].hits | length),
  values: ["both orgs OAuth app secrets", "the gateway internal secret", "every vendor access and refresh token (by their prefixes)"]}' \
  "$SCN_DIR/scan.json")"
scn_pass
