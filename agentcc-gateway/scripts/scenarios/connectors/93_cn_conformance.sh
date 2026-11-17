#!/usr/bin/env bash
# L3 connector conformance. Every provider of a catalog directory goes
# through the real gateway chain against an agentcc-mcpmock upstream that
# serves its tools (its MCP cassette's recorded tools/list when it has
# one, else the tools it declares plus one it does not), and is judged row by
# row (internal/connectorcatalog/conformance):
#  1. tools/list through virtual servers: the audit view lists every
#     served tool; the posture view, the virtual server add-from-catalog
#     pushes, lists only the tools the manifest's postures allow.
#  2. Annotations reach the caller exactly as the upstream served them.
#  3. The credential reaches the upstream on every message (the mock's
#     /__received keeps its sha256 only), no other credential does (never the
#     caller's own gateway key), and no test credential value appears in the
#     gateway's output, its request logs, the answers or the mocks' logs.
#  4. Deny posture blocks: a refused tool is hidden, its call is answered in
#     band with tool_denied, and it never reaches the upstream.
#  5. One tool_call record per call, with its org, key, virtual server,
#     server and outcome.
# A K4 (stdio) provider runs as an operator YAML stdio server of the static
# org (an org config never carries a command), and its declared
# environment must reach the child. A K3 partner provider, and every row that
# needs work not built at this base (among others, the mock's
# witnesses for a query or URL-variable credential part), is skipped
# with a plain reason; nothing is skipped silently. The gateway
# loads the run's catalogs (connectors.catalog_dir, written by l3conformance
# plan and render), so each case's catalog_ref is placed by the credential
# parts its provider declares.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<lane db> \
#     bash scripts/scenarios/connectors/93_cn_conformance.sh --catalog testdata
#   ... --catalog <catalog dir>   (relative to agentcc-gateway, or absolute)
#
# --catalog testdata runs the example catalog (manifest/testdata/valid) and
# the harness's own (conformance/testdata/catalog: a recording, stdio, SSE,
# and placements the example lacks). The plan, the evidence and the report
# (report.json) land in $SCN_HOME/scn-cn8/<this script>/, which scn_up does
# not wipe. The connector scenarios moved here,
# under connectors/ with their own runner, because every two-digit number in
# the frozen run_all.sh's glob is taken.
set -u
CATALOG=""
APPS=all
while [ $# -gt 0 ]; do
  case "$1" in
    --catalog) CATALOG=${2:-}; shift 2 ;;
    --catalog=*) CATALOG=${1#--catalog=}; shift ;;
    --apps) [ $# -ge 2 ] || { echo '--apps requires a value' >&2; exit 2; }; APPS=$2; shift 2 ;;
    --apps=*) APPS=${1#--apps=}; shift ;;
    *) echo "93_cn_conformance.sh: unknown argument $1 (want --catalog testdata|<dir>)" >&2; exit 2 ;;
  esac
done
[ -n "$CATALOG" ] || { echo "93_cn_conformance.sh: --catalog testdata|<dir> is required" >&2; exit 2; }

source "$(dirname "$0")/../lib.sh"
source "$SCN_ROOT/lib_mcp.sh"

CN8_HOME=$SCN_HOME/scn-cn8/$(basename "$0" .sh)
WORK=$CN8_HOME/work
EVID=$CN8_HOME/evidence
REPORT=$CN8_HOME/report.json
L3=$SCN_BIN/l3conformance
rm -rf "$CN8_HOME"
mkdir -p "$WORK" "$EVID" "$SCN_BIN"

if [ "$APPS" != all ]; then
  [[ "$APPS" =~ ^[a-z][a-z0-9-]*(,[a-z][a-z0-9-]*)*$ ]] || { echo 'invalid --apps list' >&2; exit 2; }
  [ "$CATALOG" != testdata ] || { echo '--apps needs a catalog directory' >&2; exit 2; }
  case "$CATALOG" in /*) source_catalog=$CATALOG ;; *) source_catalog=$SCN_GATEWAY_DIR/$CATALOG ;; esac
  subset=$CN8_HOME/selected-catalog
  mkdir -p "$subset/apps"
  [ ! -d "$source_catalog/platforms" ] || cp -R "$source_catalog/platforms" "$subset/"
  IFS=, read -r -a selected <<< "$APPS"
  for app in "${selected[@]}"; do
    [ -d "$source_catalog/apps/$app" ] || { echo "unknown app $app" >&2; exit 2; }
    [ -d "$subset/apps/$app" ] || cp -R "$source_catalog/apps/$app" "$subset/apps/" || exit 1
  done
  CATALOG=$subset
fi

if [ -z "${SCN_SKIP_BUILD:-}" ] || [ ! -x "$L3" ]; then
  (cd "$SCN_GATEWAY_DIR" && go build -o "$L3" ./internal/connectorcatalog/conformance/cmd/l3conformance) \
    >"$CN8_HOME/build.log" 2>&1 || scn_fail cn8.build "building l3conformance failed: $(tail -5 "$CN8_HOME/build.log")"
fi

scn_check "0. plan a case per provider of --catalog $CATALOG"
"$L3" plan --catalog "$CATALOG" --gateway-dir "$SCN_GATEWAY_DIR" --work "$WORK" >"$CN8_HOME/plan.log" 2>&1 ||
  scn_fail cn8.plan "$(cat "$CN8_HOME/plan.log")"
cat "$CN8_HOME/plan.log"
PLAN=$WORK/plan.json
ERA=$(jq -r .inbound_era "$PLAN") # every session opens with it, every call carries it (MCP-Protocol-Version)
RUNNING=$(jq '[.cases[] | select(.skip == null)] | length' "$PLAN")
[ "$RUNNING" -gt 0 ] || scn_fail cn8.plan "no case runs: $(cat "$CN8_HOME/plan.log")"

# --- one mock per http case, then the gateway ---------------------------------
echo '{}' >"$WORK/mocks.json"
while IFS= read -r sid; do
  args=()
  while IFS= read -r a; do args+=("$a"); done < <(jq -r --arg s "$sid" '.cases[] | select(.server_id == $s) | .mock_args[]' "$PLAN")
  scn_mcp_up "$sid" --fixtures "$WORK/$sid" "${args[@]}" </dev/null
  url=$(_scn_mcp_url "$sid") || scn_fail cn8.mock "no URL for mock $sid"
  jq --arg s "$sid" --arg u "$url" '. + {($s): $u}' "$WORK/mocks.json" >"$WORK/mocks.json.new" && mv "$WORK/mocks.json.new" "$WORK/mocks.json"
done < <(jq -r '.cases[] | select(.skip == null and .transport != "stdio") | .server_id' "$PLAN")

"$L3" render --work "$WORK" --mocks "$WORK/mocks.json" --base "$SCN_ROOT/fixtures/connectors/gateway.yaml" \
  --out "$SCN_BIN/cn8-gateway.yaml" || scn_fail cn8.render "l3conformance render failed"
scn_mcp_gateway_template "$SCN_BIN/cn8-gateway.yaml"
# shellcheck disable=SC2119 # scn_up takes no arguments
SCN_CATALOG_WAIT_SKIP=all scn_up # no model provider: the harness makes no model call

# --- two keys per case, and the org configs add-from-catalog would push --------
sha() { printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1; }
echo '{}' >"$WORK/keys.json"
while IFS=$'\t' read -r cid sid org; do
  mkdir -p "$EVID/$sid/calls"
  kp="" ka="" kp_id="" ka_id="" # scn_key fails inside $(...) without stopping the script
  kp=$(scn_key "$org") && kp_id=$(jq -r .id "$SCN_DIR/last.body")
  ka=$(scn_key "$org") && ka_id=$(jq -r .id "$SCN_DIR/last.body")
  case "$kp_id$ka_id" in key_*key_*) ;; *) scn_fail cn8.keys "$cid: no key ids ($kp_id, $ka_id)" ;; esac
  jq -n --arg p "$kp_id" --arg a "$ka_id" --arg h1 "$(sha "Bearer $kp")" --arg h2 "$(sha "$kp")" \
    --arg h3 "$(sha "Bearer $ka")" --arg h4 "$(sha "$ka")" \
    '{posture: $p, audit: $a, inbound_sha256: [$h1, $h2, $h3, $h4]}' >"$EVID/$sid/keys.json"
  printf '%s\n%s\n' "$kp" "$ka" >"$WORK/$sid.keys" # the raw keys stay out of the evidence
  jq --arg c "$cid" --arg p "$kp_id" --arg a "$ka_id" '. + {($c): {posture: $p, audit: $a}}' "$WORK/keys.json" \
    >"$WORK/keys.json.new" && mv "$WORK/keys.json.new" "$WORK/keys.json"
done < <(jq -r '.cases[] | select(.skip == null) | [.id, .server_id, .org] | @tsv' "$PLAN")

"$L3" orgs --work "$WORK" --mocks "$WORK/mocks.json" --keys "$WORK/keys.json" --out "$WORK/orgs" >"$WORK/orgs.txt" ||
  scn_fail cn8.orgs "l3conformance orgs failed"
while IFS= read -r org; do
  scn_org_config "$org" "$(cat "$WORK/orgs/$org.json")"
done <"$WORK/orgs.txt"

# cn8_list <vs id> <out>: tools/list through the virtual server's path, every
# page merged into {"tools": [...]}. Returns non-zero on a failed page.
cn8_list() {
  local vs=$1 out=$2 cursor="" body code pages=0
  echo '{"tools":[]}' >"$out"
  while :; do
    if [ -n "$cursor" ]; then
      body=$(jq -nc --arg c "$cursor" '{jsonrpc:"2.0",id:2,method:"tools/list",params:{cursor:$c}}')
    else
      body='{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
    fi
    code=$(scn_mcp_post "/mcp/v/$vs" "$body" "MCP-Protocol-Version: $ERA")
    [ "$code" = 200 ] && jq -e '.result.tools' "$SCN_DIR/last.body" >/dev/null 2>&1 || return 1
    jq -s '{tools: (.[0].tools + .[1].result.tools)}' "$out" "$SCN_DIR/last.body" >"$out.new" && mv "$out.new" "$out"
    cursor=$(jq -r '.result.nextCursor // ""' "$SCN_DIR/last.body")
    pages=$((pages + 1))
    [ -z "$cursor" ] || [ $pages -ge 50 ] && return 0
  done
}

# cn8_session <key> <vs id>: a legacy session on the virtual server's path
# (the gateway binds a session to the path it was opened on).
cn8_session() {
  SCN_KEY=$1
  SCN_MCP_SESSION=""
  scn_mcp_session_init "$ERA" "/mcp/v/$2"
}

# cn8_call <vs id> <tool> <arguments json> <out>: one tools/call, its answer kept.
cn8_call() {
  local code
  code=$(scn_mcp_post "/mcp/v/$1" "$(jq -nc --arg n "$2" --argjson a "$3" \
    '{jsonrpc:"2.0",id:3,method:"tools/call",params:{name:$n,arguments:$a}}')" "MCP-Protocol-Version: $ERA")
  [ "$code" = 200 ] || scn_fail "cn8.call.$2" "tools/call $2 returned HTTP $code: $(cat "$SCN_DIR/last.body")"
  cp "$SCN_DIR/last.body" "$4"
}

# --- each case: list, call, collect -------------------------------------------
TOTAL_CALLS=0
while IFS=$'\t' read -r cid sid transport posture_vs audit_vs want_audit env_probe; do
  scn_check "case $cid"
  kp=$(sed -n 1p "$WORK/$sid.keys")
  ka=$(sed -n 2p "$WORK/$sid.keys")
  # The org's pool connects in the background: wait for the audit view.
  deadline=$(( $(date +%s) + 30 ))
  while :; do
    cn8_session "$ka" "$audit_vs"
    if cn8_list "$audit_vs" "$EVID/$sid/list-audit.json" &&
      [ "$(jq '.tools | length' "$EVID/$sid/list-audit.json")" -ge "$want_audit" ]; then
      break
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "  the audit view never listed $want_audit tools: $(cat "$SCN_DIR/last.body")"
      break
    fi
    sleep 0.5
  done
  if [ -n "$env_probe" ]; then
    cn8_call "$audit_vs" "$env_probe" '{}' "$EVID/$sid/env.json"
    TOTAL_CALLS=$((TOTAL_CALLS + 1))
  fi
  cn8_session "$kp" "$posture_vs"
  cn8_list "$posture_vs" "$EVID/$sid/list-posture.json" || echo "  the posture view's tools/list failed: $(cat "$SCN_DIR/last.body")"
  while IFS=$'\t' read -r exposed args; do
    cn8_call "$posture_vs" "$exposed" "$args" "$EVID/$sid/calls/$exposed.json"
    TOTAL_CALLS=$((TOTAL_CALLS + 1))
  done < <(jq -r --arg c "$cid" '.cases[] | select(.id == $c) | .tools[] | [.exposed, (.arguments | tojson)] | @tsv' "$PLAN")
  if [ "$transport" = stdio ]; then
    addr_file=$WORK/$sid/control.addr
    [ -s "$addr_file" ] || scn_fail "cn8.$sid.control" "the stdio mock wrote no control address"
    curl -sS --max-time 10 "http://$(cat "$addr_file")/__received" >"$EVID/$sid/received.json"
  else
    scn_mcp_received "$sid" >"$EVID/$sid/received.json"
  fi
  echo "  collected $(jq '.received | length' "$EVID/$sid/received.json") upstream messages"
done < <(jq -r '.cases[] | select(.skip == null) |
  [.id, .server_id, .transport, .posture.id, .audit.id,
   ((.tools | length) + (if .env_probe then 1 else 0 end)), (.env_probe // "")] | @tsv' "$PLAN")

# --- the records, the logs, then the verdict ------------------------------------
scn_check "records: wait for $TOTAL_CALLS tool_call records"
deadline=$(( $(date +%s) + 30 ))
while :; do
  curl -sS "$SCN_MOCK/__logs" >"$EVID/logs.json"
  n=$(jq '[.logs[] | select(.kind == "tool_call")] | length' "$EVID/logs.json")
  [ "$n" -ge "$TOTAL_CALLS" ] && break
  if [ "$(date +%s)" -ge "$deadline" ]; then echo "  only $n records after 30 s"; break; fi
  sleep 0.5
done
sleep 1 # one more flush: a record past the count would be a second record of some call
curl -sS "$SCN_MOCK/__logs" >"$EVID/logs.json"
scn_gateway_log >"$EVID/gateway.log"
mkdir -p "$EVID/mock-logs"
cp "$SCN_MCP_DIR"/*.log "$EVID/mock-logs/" 2>/dev/null

scn_check "verdict"
"$L3" check --work "$WORK" --evidence "$EVID" --report "$REPORT" | tee "$CN8_HOME/report.txt"
status=${PIPESTATUS[0]}
scn_assert_eq l3.rows_failed 0 "$(jq .rows_fail "$REPORT")"
scn_assert_eq l3.check_exit 0 "$status"
scn_assert_jq l3.every_case_judged '.cases | length' "$(jq '.cases | length' "$PLAN")" "$REPORT"
scn_pass
