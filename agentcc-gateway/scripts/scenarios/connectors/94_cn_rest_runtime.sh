#!/usr/bin/env bash
# The K2 runtime, agentcc-connectors, serving the example Gmail app
# against a recorded, Gmail-shaped cassette (fixtures/CN10/
# gmail.cassette.json, replayed by agentcc-cassette), with real binaries:
#  0. the cassette passes agentcc-cassette check; the runtime starts on the example
#     example catalog and /healthz names its apps;
#  1. a request without the user's token gets 401 with WWW-Authenticate, and
#     the vendor gets nothing;
#  2. the gateway's own MCP client (internal/mcp), pinned to 2025-11-25
#     and to 2026-07-28, lists the tools with the manifest's annotations and
#     makes calls.json's calls, each with a per-call bearer
#     (CallOptions.Auth, how the vendors client sends a user's token per call), alternating
#     two users: the vendor stand-in sees exactly that call's token on each
#     request (k2drive's witness keeps SHA-256 only);
#  3. the real gateway, with the runtime as a static YAML MCP server, lists
#     the runtime's tools through /mcp and calls one: the vendor gets the
#     server's bearer (never the caller's gateway key), and the gateway
#     writes one tool_call record;
#  4. the replay answered every request from the recording (exit 0, no
#     miss), and the runtime drains and exits 0 on SIGTERM;
#  5. no token appears in the runtime's log, the gateway's log, the replay's
#     or the witness's output, or any evidence file.
# At this base the gateway binary has no path that sends a per-user token
# so step 2 drives the upstream client's CallOptions.Auth directly, and step 3 the
# real gateway with a server bearer.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<lane db> \
#     bash scripts/scenarios/connectors/94_cn_rest_runtime.sh
#   ... --catalog <catalog dir> --apps <app,...>|all
#
# With no arguments it runs the steps above. Evidence lands in
# $SCN_HOME/scn-cn10/<this script>/, which scn_up does not wipe. The
# connector scenarios live here, under connectors/
# with their own runner.
#
# With --catalog (relative to agentcc-gateway, or absolute) and --apps, it
# replays each named app's committed K2 cassette through the runtime on that
# catalog instead: steps 0-2, 4 and 5 for every app, with the app's own
# expectations from fixtures/K2-replay/<app>.json (connectors/lib_k2_replay.sh
# says what it checks). `--apps all` takes every
# app whose rest provider has a committed cassette. connectors/run_all.sh runs
# both: no arguments, then `--catalog catalog/connectors --apps all`.
set -u
K2_CATALOG=""
K2_APPS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --catalog | --apps)
      [ $# -ge 2 ] || { echo "94_cn_rest_runtime.sh: $1 needs a value" >&2; exit 2; }
      if [ "$1" = --catalog ]; then K2_CATALOG=$2; else K2_APPS=$2; fi
      shift 2 ;;
    --catalog=*) K2_CATALOG=${1#--catalog=}; shift ;;
    --apps=*) K2_APPS=${1#--apps=}; shift ;;
    *) echo "94_cn_rest_runtime.sh: unknown argument $1 (want none, or --catalog <dir> --apps <app,...>|all)" >&2; exit 2 ;;
  esac
done
if [ -n "$K2_CATALOG" ] || [ -n "$K2_APPS" ]; then
  [ -n "$K2_CATALOG" ] && [ -n "$K2_APPS" ] ||
    { echo "94_cn_rest_runtime.sh: --catalog and --apps go together" >&2; exit 2; }
fi

source "$(dirname "$0")/../lib.sh"
source "$SCN_ROOT/lib_mcp.sh"

if [ -n "$K2_CATALOG" ]; then
  source "$SCN_ROOT/connectors/lib_k2_replay.sh"
  k2_replay_main "$K2_CATALOG" "$K2_APPS"
  exit 0
fi

CN10_HOME=$SCN_HOME/scn-cn10/$(basename "$0" .sh)
WORK=$CN10_HOME/work
EVID=$CN10_HOME/evidence
FIX=$SCN_ROOT/fixtures/CN10
CATALOG=$SCN_GATEWAY_DIR/../scripts/connector-gen/rest/tests/fixtures/catalog
GMAIL_ORIGIN=https://gmail.googleapis.com
rm -rf "$CN10_HOME"
mkdir -p "$WORK" "$EVID" "$SCN_BIN"

cn10_build() { # binary package
  [ -n "${SCN_SKIP_BUILD:-}" ] && [ -x "$SCN_BIN/$1" ] && return 0
  (cd "$SCN_GATEWAY_DIR" && go build -o "$SCN_BIN/$1" "$2") >>"$CN10_HOME/build.log" 2>&1 ||
    scn_fail "cn10.build.$1" "building $2 failed: $(tail -5 "$CN10_HOME/build.log")"
}
cn10_build agentcc-connectors ./cmd/agentcc-connectors
cn10_build agentcc-cassette ./cmd/agentcc-cassette
cn10_build k2drive ./internal/connectorcatalog/restmcp/k2drive

# Fresh fake tokens for this run: never a real credential, never in a file
# this scenario writes (step 5 checks).
tok() { python3 -I -c 'import secrets; print("ya29.cn10-scn-" + secrets.token_hex(16))'; }
CN10_TOKEN_A=$(tok)
CN10_TOKEN_B=$(tok)
CN10_SERVER_TOKEN=$(tok)
export CN10_TOKEN_A CN10_TOKEN_B
sha() { printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1; }

# cn10_start <name> <command...>: start a process in the background (output
# in $WORK/<name>.log), stopped on exit with the MCP mocks (lib_mcp.sh).
cn10_start() {
  local name=$1
  shift
  "$@" >"$WORK/$name.log" 2>&1 &
  CN10_PID=$!
  _SCN_MCP_PIDS+=("$CN10_PID")
}
trap _scn_mcp_down EXIT

# cn10_addr <name> <pid>: wait for <name>'s address file and print it.
cn10_addr() {
  local deadline=$(($(date +%s) + 15))
  until [ -s "$WORK/$1.addr" ]; do
    kill -0 "$2" 2>/dev/null || { echo "$1 exited: $(tail -5 "$WORK/$1.log")" >&2; return 1; }
    [ "$(date +%s)" -ge "$deadline" ] && { echo "$1 wrote no address: $(tail -5 "$WORK/$1.log")" >&2; return 1; }
    sleep 0.1
  done
  tr -d '\n' <"$WORK/$1.addr"
}

scn_check "0. the cassette, the replay, the witness and the runtime"
"$SCN_BIN/agentcc-cassette" check "$FIX/gmail.cassette.json" >"$WORK/check.log" 2>&1
scn_assert_eq 0.cassette_check 0 "$?"
cn10_start replay "$SCN_BIN/agentcc-cassette" replay --cassette "$FIX/gmail.cassette.json" --addr 127.0.0.1:0 \
  --addr-file "$WORK/replay.addr"
REPLAY_PID=$CN10_PID
REPLAY=$(cn10_addr replay "$REPLAY_PID") || scn_fail cn10.replay "the replay did not start"
cn10_start witness "$SCN_BIN/k2drive" witness --upstream "http://$REPLAY" --addr 127.0.0.1:0 \
  --addr-file "$WORK/witness.addr" --out "$EVID/vendor-requests.jsonl"
WITNESS=$(cn10_addr witness "$CN10_PID") || scn_fail cn10.witness "the witness did not start"
cn10_start runtime "$SCN_BIN/agentcc-connectors" --addr 127.0.0.1:0 --addr-file "$WORK/runtime.addr" \
  --catalog-dir "$CATALOG" --replay "$GMAIL_ORIGIN=http://$WITNESS"
RUNTIME_PID=$CN10_PID
RUNTIME=$(cn10_addr runtime "$RUNTIME_PID") || scn_fail cn10.runtime "the runtime did not start"
RT=http://$RUNTIME
curl -sS --max-time 10 "$RT/healthz" >"$EVID/healthz.json"
scn_assert_jq 0.healthz_status '.status' ok "$EVID/healthz.json"
scn_assert_jq 0.healthz_serves_gmail '.apps | index("gmail") != null' true "$EVID/healthz.json"
scn_assert_jq 0.healthz_apps '.apps | length' 6 "$EVID/healthz.json"

scn_check "1. no user token: 401 with WWW-Authenticate, nothing sent to the vendor"
code=$(curl -sS --max-time 10 -o "$WORK/noauth.body" -D "$WORK/noauth.headers" -w '%{http_code}' -X POST "$RT/gmail/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-11-25' \
  --data-binary '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_profile","arguments":{}}}')
scn_assert_eq 1.status 401 "$code"
scn_assert_eq 1.challenge yes "$(grep -qi '^WWW-Authenticate: Bearer realm="gmail"' "$WORK/noauth.headers" && echo yes || echo no)"
scn_assert_eq 1.vendor_untouched 0 "$(wc -l <"$EVID/vendor-requests.jsonl" | tr -d ' ')"

scn_check "2. the gateway's MCP client, a per-call bearer on each call, both eras"
NCALLS=$(jq length "$FIX/calls.json")
for era in 2025-11-25 2026-07-28; do
  before=$(wc -l <"$EVID/vendor-requests.jsonl" | tr -d ' ')
  "$SCN_BIN/k2drive" drive --url "$RT/gmail/mcp" --era "$era" --calls "$FIX/calls.json" \
    --conn-token-env CN10_TOKEN_A --call-token-env CN10_TOKEN_A --call-token-env CN10_TOKEN_B \
    --out "$EVID/drive-$era.json" >"$WORK/drive-$era.log" 2>&1
  scn_assert_eq "2.$era.drive_exit" 0 "$?"
  D=$EVID/drive-$era.json
  scn_assert_jq "2.$era.negotiated" '.era' "$era" "$D"
  scn_assert_jq "2.$era.tools" '.tools | length' 10 "$D"
  scn_assert_jq "2.$era.trash_destructive" '.tools[] | select(.name == "trash_message") | .annotations.destructiveHint' true "$D"
  scn_assert_jq "2.$era.profile_read_only" '.tools[] | select(.name == "get_profile") | .annotations.readOnlyHint' true "$D"
  scn_assert_jq "2.$era.no_generator_keys" '[.tools[] | .inputSchema | tostring | test("\"x-")] | any' false "$D"
  scn_assert_jq "2.$era.calls" '.calls | length' "$NCALLS" "$D"
  scn_assert_jq "2.$era.no_client_errors" '[.calls[] | select(.error)] | length' 0 "$D"
  scn_assert_jq "2.$era.successes" '[.calls[0:5][] | select(.result.isError == false)] | length' 5 "$D"
  scn_assert_jq "2.$era.search_threads" '.calls[1].result.content[0].text | contains("Quarterly numbers")' true "$D"
  scn_assert_jq "2.$era.send_message" '.calls[4].result.content[0].text | contains("SENT")' true "$D"
  scn_assert_jq "2.$era.vendor_404_is_error" '.calls[5].result.isError' true "$D"
  scn_assert_jq "2.$era.vendor_404_attributed" '.calls[5].result._meta["com.futureagi/vendor"] | [.name, .host, .status] | join(" ")' \
    "Google gmail.googleapis.com 404" "$D"
  # Each vendor request carried exactly its own call's token.
  tail -n +"$((before + 1))" "$EVID/vendor-requests.jsonl" >"$WORK/vendor-$era.jsonl"
  scn_assert_eq "2.$era.vendor_requests" "$NCALLS" "$(wc -l <"$WORK/vendor-$era.jsonl" | tr -d ' ')"
  jq -n --arg a "$(sha "Bearer $CN10_TOKEN_A")" --arg b "$(sha "Bearer $CN10_TOKEN_B")" --slurpfile d "$D" \
    '[$d[0].calls[] | if .token_index == 0 then $a else $b end]' >"$WORK/want-$era.json"
  scn_assert_eq "2.$era.per_call_bearer" "$(jq -c . "$WORK/want-$era.json")" \
    "$(jq -sc '[.[].authorization_sha256]' "$WORK/vendor-$era.jsonl")"
done

scn_check "3. the real gateway calls the runtime as a YAML MCP server"
sed -e "s/@@CN10_RUNTIME_PORT@@/${RUNTIME##*:}/g" -e "s/@@CN10_SERVER_TOKEN@@/$CN10_SERVER_TOKEN/" \
  "$FIX/gateway.yaml" >"$SCN_BIN/cn10-gateway.yaml"
scn_mcp_gateway_template "$SCN_BIN/cn10-gateway.yaml"
# shellcheck disable=SC2119 # scn_up takes no arguments
SCN_CATALOG_WAIT_SKIP=all scn_up # no model provider: no model call is made
scn_mcp_session_init 2025-11-25
deadline=$(($(date +%s) + 30))
while :; do
  code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')
  [ "$code" = 200 ] && [ "$(jq '[.result.tools[]? | select(.name | endswith("get_profile"))] | length' "$SCN_DIR/last.body")" = 1 ] && break
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail cn10.gateway_list "the gateway never listed the runtime's tools: $(cat "$SCN_DIR/last.body")"
  sleep 0.5
done
cp "$SCN_DIR/last.body" "$EVID/gateway-tools-list.json"
scn_assert_jq 3.gateway_tools '.result.tools | length' 10 "$EVID/gateway-tools-list.json"
PROFILE=$(jq -r '.result.tools[] | select(.name | endswith("get_profile")) | .name' "$EVID/gateway-tools-list.json")
scn_assert_jq 3.annotations_read_only ".result.tools[] | select(.name == \"$PROFILE\") | .annotations.readOnlyHint" true \
  "$EVID/gateway-tools-list.json"
scn_assert_jq 3.annotations_destructive '.result.tools[] | select(.name | endswith("trash_message")) | .annotations.destructiveHint' \
  true "$EVID/gateway-tools-list.json"
before=$(wc -l <"$EVID/vendor-requests.jsonl" | tr -d ' ')
code=$(scn_mcp_post /mcp "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"$PROFILE\",\"arguments\":{}}}")
cp "$SCN_DIR/last.body" "$EVID/gateway-tools-call.json"
scn_assert_eq 3.call_status 200 "$code"
scn_assert_jq 3.call_ok '.result.isError' false "$EVID/gateway-tools-call.json"
scn_assert_jq 3.call_result '.result.content[0].text | contains("emailAddress")' true "$EVID/gateway-tools-call.json"
tail -n +"$((before + 1))" "$EVID/vendor-requests.jsonl" | jq -s . >"$WORK/vendor-gateway.json"
scn_assert_jq 3.vendor_got_one 'length' 1 "$WORK/vendor-gateway.json"
scn_assert_jq 3.vendor_got_server_bearer '.[0].authorization_sha256' "$(sha "Bearer $CN10_SERVER_TOKEN")" "$WORK/vendor-gateway.json"
scn_assert_eq 3.gateway_key_not_forwarded 0 \
  "$(grep -c -e "$(sha "Bearer $SCN_KEY")" -e "$(sha "$SCN_KEY")" "$EVID/vendor-requests.jsonl")"
deadline=$(($(date +%s) + 30))
while :; do
  curl -sS --max-time 10 "$SCN_MOCK/__logs" >"$EVID/request-logs.json"
  n=$(jq '[.logs[] | select(.kind == "tool_call")] | length' "$EVID/request-logs.json")
  [ "$n" -ge 1 ] && break
  [ "$(date +%s)" -ge "$deadline" ] && break
  sleep 0.5
done
scn_assert_eq 3.tool_call_record 1 "$(jq '[.logs[] | select(.kind == "tool_call")] | length' "$EVID/request-logs.json")"
scn_assert_eq 3.record_names_the_tool yes \
  "$(jq -c '[.logs[] | select(.kind == "tool_call")]' "$EVID/request-logs.json" | grep -q get_profile && echo yes || echo no)"

scn_check "4. the replay missed nothing; the runtime drains and exits 0"
kill -TERM "$REPLAY_PID"
wait "$REPLAY_PID"
scn_assert_eq 4.replay_exit_no_miss 0 "$?"
kill -TERM "$RUNTIME_PID"
wait "$RUNTIME_PID"
scn_assert_eq 4.runtime_exit 0 "$?"
cp "$WORK/runtime.log" "$EVID/runtime.log"
cp "$WORK/replay.log" "$EVID/replay.log"
scn_assert_eq 4.runtime_stopped yes "$(grep -q '"msg":"agentcc-connectors stopped"' "$EVID/runtime.log" && echo yes || echo no)"
# 1 unauthenticated request, 2 eras x (initialize + tools/list + the calls),
# and the gateway's handshake, list and call: every one logged.
scn_assert_eq 4.calls_logged "$((2 * NCALLS + 1))" "$(grep -c '"method":"tools/call"' "$EVID/runtime.log")"
scn_assert_eq 4.unauthenticated_logged 1 "$(grep -c '"outcome":"unauthenticated"' "$EVID/runtime.log")"

scn_check "5. no token in any log or evidence file"
scn_gateway_log >"$EVID/gateway.log"
leaks=0
for f in "$EVID"/* "$WORK"/*.log "$WORK"/*.json*; do
  for secret in "$CN10_TOKEN_A" "$CN10_TOKEN_B" "$CN10_SERVER_TOKEN"; do
    if grep -qF "$secret" "$f"; then
      echo "  $(basename "$f") holds a token"
      leaks=$((leaks + 1))
    fi
  done
done
scn_assert_eq 5.no_token_anywhere 0 "$leaks"
scn_assert_eq 5.runtime_log_no_token 0 "$(grep -cF -e "$CN10_TOKEN_A" -e "$CN10_TOKEN_B" -e "$CN10_SERVER_TOKEN" "$EVID/runtime.log")"
scn_pass
