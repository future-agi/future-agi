#!/usr/bin/env bash
# A YAML stdio MCP server stays alive, sees only its declared environment
# and shuts down gracefully with the gateway.
#  1. The server stays listed, healthy and callable (/-/mcp/status, /-/mcp/tools,
#     /-/mcp/test) for 60 s, on one child process.
#  2. Its env_names tool returns only the declared names, while the gateway runs
#     with AGENTCC_ADMIN_TOKEN and a fake provider key set. The server
#     declares no env of its own, so the declared names are the default
#     base_env: PATH, HOME, LANG, TMPDIR.
#  3. Stopping the gateway leaves the child's graceful-shutdown marker and no
#     child process.
#
#   bash scripts/scenarios/63_stdio.sh
#
# The child: SCN_STDIO_CMD, a command line in which "{marker}" becomes the
# marker path (without it, "--marker <path>" is appended). Unset, it is
# agentcc-mcpmock --mode stdio when cmd/agentcc-mcpmock exists, and the
# stand-in fixtures/stdio/stdiochild otherwise.
#
# The gateway is started here, not with scn_up, because scn_up unsets every
# AGENTCC_* variable and this gateway needs AGENTCC_ADMIN_TOKEN and
# AGENTCC_MCP_STATE_KEYS (the gateway refuses mcp.enabled without a state key). It
# makes no model call, so it starts no mock upstream and uses no Redis.
set -u
source "$(dirname "$0")/lib.sh"

STDIO_SERVER=stdiosrv
STDIO_WINDOW_S=60
STDIO_POLL_S=5
STDIO_YAML_ADMIN_TOKEN=$SCN_ADMIN_TOKEN # rendered into the YAML
STDIO_ENV_ADMIN_TOKEN=scn-stdio-env-9d2f6a1c8e4b7f3a0c5e2d9b6f1a8c4e # 32+ characters (internal/config/admin_token.go)
STDIO_STATE_KEY=scn-stdio-fixed-state-key-not-a-secret-0001 # at least 32 bytes
STDIO_PROVIDER_KEY=scn-fake-provider-key-for-stdio
STDIO_MARKER=""
STDIO_GW_PID=""
STDIO_CHILD=()

# stdio_child: set STDIO_CHILD to the child's command line (split on spaces), with
# the marker path in it.
stdio_child() {
  local cmd
  if [ -n "${SCN_STDIO_CMD:-}" ]; then
    cmd=$SCN_STDIO_CMD
  elif [ -d "$SCN_GATEWAY_DIR/cmd/agentcc-mcpmock" ]; then
    stdio_build agentcc-mcpmock ./cmd/agentcc-mcpmock
    cmd="$SCN_BIN/agentcc-mcpmock --mode stdio"
  else
    stdio_build stdiochild ./scripts/scenarios/fixtures/stdio/stdiochild
    cmd="$SCN_BIN/stdiochild --mode stdio"
  fi
  case "$cmd" in
    *"{marker}"*) cmd=${cmd//\{marker\}/$STDIO_MARKER} ;;
    *) cmd="$cmd --marker $STDIO_MARKER" ;;
  esac
  read -r -a STDIO_CHILD <<<"$cmd"
}

stdio_build() { # binary package
  [ -n "${SCN_SKIP_BUILD:-}" ] && [ -x "$SCN_BIN/$1" ] && return 0
  (cd "$SCN_GATEWAY_DIR" && go build -o "$SCN_BIN/$1" "$2") >>"$SCN_DIR/build.log" 2>&1 ||
    scn_fail stdio_up "building $1 failed: $(tail -5 "$SCN_DIR/build.log")"
}

# stdio_child_pids: the running children (their argv holds the marker path).
stdio_child_pids() { pgrep -f -- "$STDIO_MARKER" | sort -n | tr '\n' ' ' | sed 's/ $//'; }

stdio_up() {
  local tool gw_port
  for tool in go curl jq python3 pgrep; do
    command -v "$tool" >/dev/null || scn_fail stdio_up "$tool is required"
  done
  rm -rf "$SCN_DIR"
  mkdir -p "$SCN_DIR" "$SCN_BIN"
  STDIO_MARKER=$SCN_DIR/stdio-graceful.marker
  # A run killed with SIGKILL (or a reboot) can leave its child behind; it
  # would count as a second child here.
  pkill -9 -f -- "$STDIO_MARKER" 2>/dev/null
  trap stdio_down EXIT
  trap 'exit 130' INT TERM
  stdio_build agentcc ./cmd/agentcc
  stdio_child
  command -v "${STDIO_CHILD[0]}" >/dev/null || scn_fail stdio_up "no stdio child command ${STDIO_CHILD[0]}"

  gw_port=$(_scn_free_port)
  SCN_GW="http://127.0.0.1:$gw_port"
  SCN_T_GATEWAY_PORT=$gw_port SCN_T_GATEWAY_URL=$SCN_GW SCN_T_MOCK_URL=http://127.0.0.1:9 \
    SCN_T_ADMIN_TOKEN=$STDIO_YAML_ADMIN_TOKEN SCN_T_SCN_DIR=$SCN_DIR SCN_T_SCN_ROOT=$SCN_ROOT SCN_T_RUN=$SCN_RUN \
    SCN_T_REDIS="" SCN_T_REDIS_DB=0 SCN_T_OTEL="" \
    _scn_render_config "$SCN_ROOT/fixtures/stdio/gateway.yaml" "$SCN_DIR/config.yaml"
  python3 -I - "$SCN_DIR/config.yaml" "$STDIO_SERVER" "${STDIO_CHILD[@]}" <<'PY' || scn_fail stdio_up "cannot append the mcp block"
import json, sys
path, server, command, args = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
with open(path, "a", encoding="utf-8") as f:
    f.write("mcp:\n  enabled: true\n  servers:\n    %s:\n      transport: \"stdio\"\n"
            "      command: %s\n      args: %s\n" % (server, json.dumps(command), json.dumps(args)))
PY

  local unset_env=() var
  for var in $(env | sed -n 's/^\(AGENTCC_[A-Za-z0-9_]*\)=.*/\1/p'); do unset_env+=(-u "$var"); done
  env ${unset_env[@]+"${unset_env[@]}"} AGENTCC_PORT="$gw_port" AGENTCC_ADMIN_TOKEN="$STDIO_ENV_ADMIN_TOKEN" \
    AGENTCC_MCP_STATE_KEYS="$STDIO_STATE_KEY" OPENAI_API_KEY="$STDIO_PROVIDER_KEY" \
    "$SCN_BIN/agentcc" --config "$SCN_DIR/config.yaml" >"$SCN_DIR/gateway.log" 2>&1 &
  STDIO_GW_PID=$!
  _scn_wait_http gateway "$SCN_GW/healthz" 60 "$STDIO_GW_PID"
  SCN_ADMIN_TOKEN=$STDIO_ENV_ADMIN_TOKEN # the YAML's token is overridden by the environment's
  export SCN_GW SCN_DIR
  echo "stdio_up: gateway $SCN_GW (pid $STDIO_GW_PID), child ${STDIO_CHILD[*]}, logs in $SCN_DIR"
}

# stdio_stop_gateway: SIGTERM the gateway and wait up to 30 s for it to exit.
stdio_stop_gateway() {
  [ -n "$STDIO_GW_PID" ] || return 0
  kill "$STDIO_GW_PID" 2>/dev/null
  local i=0
  while kill -0 "$STDIO_GW_PID" 2>/dev/null && [ $i -lt 150 ]; do sleep 0.2; i=$((i + 1)); done
  if kill -0 "$STDIO_GW_PID" 2>/dev/null; then
    kill -9 "$STDIO_GW_PID" 2>/dev/null
    STDIO_GW_PID=""
    return 1
  fi
  STDIO_GW_PID=""
}

# stdio_down: stop the gateway, and kill any child a failed run left behind.
stdio_down() {
  stdio_stop_gateway
  [ -n "$STDIO_MARKER" ] && pkill -9 -f -- "$STDIO_MARKER" 2>/dev/null
  return 0
}

stdio_up

scn_check "1. the stdio server stays listed, healthy and callable for $STDIO_WINDOW_S s, on one child"
pids=$(stdio_child_pids)
scn_assert_eq 1.one_child_at_start 1 "$(echo "$pids" | wc -w | tr -d ' ')"
start=$(date +%s)
tool=""
while :; do
  at=$(( $(date +%s) - start ))
  code=$(scn_admin GET /-/mcp/status)
  scn_assert_eq "1.status@${at}s" 200 "$code"
  scn_assert_jq "1.healthy@${at}s" "[.servers[] | select(.server_id == \"$STDIO_SERVER\") | .healthy] | tostring" "[true]"
  code=$(scn_admin GET /-/mcp/tools)
  scn_assert_eq "1.tools@${at}s" 200 "$code"
  tool=$(jq -r --arg s "$STDIO_SERVER" '[.[] | select(.server == $s and .original_name == "env_names") | .name] | first // ""' \
    <"$SCN_DIR/last.body")
  scn_assert_eq "1.listed@${at}s" "${STDIO_SERVER}_env_names" "$tool"
  code=$(scn_admin POST /-/mcp/test "{\"name\": \"$tool\", \"arguments\": {}}")
  scn_assert_eq "1.test_status@${at}s" 200 "$code"
  scn_assert_jq "1.callable@${at}s" '(.error // "") + "|" + (.is_error | tostring)' "|false"
  cp "$SCN_DIR/last.body" "$SCN_DIR/env_names.json"
  scn_assert_eq "1.same_child@${at}s" "$pids" "$(stdio_child_pids)"
  [ "$at" -ge "$STDIO_WINDOW_S" ] && break
  sleep "$STDIO_POLL_S"
done

scn_check "2. env_names shows only the default base_env, with the admin token and a provider key in the gateway's environment"
# The admin calls above used AGENTCC_ADMIN_TOKEN's value; the YAML's token is
# refused, so the variable is in the gateway's environment.
scn_assert_eq 2.yaml_admin_token_overridden 401 "$(SCN_ADMIN_TOKEN=$STDIO_YAML_ADMIN_TOKEN scn_admin GET /-/mcp/status)"
# The names, from the tool's text: a JSON array (the stand-in's), a JSON
# object's keys, or a list split on commas and white space.
names=$(python3 -I - "$SCN_DIR/env_names.json" <<'PY'
import json, re, sys
text = "".join(p.get("text", "") for p in json.load(open(sys.argv[1]))["content"])
try:
    v = json.loads(text)
    names = list(v) if isinstance(v, (list, dict)) else re.split(r"[\s,]+", text)
except ValueError:
    names = re.split(r"[\s,]+", text)
print(" ".join(sorted(n for n in names if n)))
PY
)
echo "  child environment names: $names"
undeclared=$(printf '%s\n' $names | grep -vxE 'PATH|HOME|LANG|TMPDIR' | tr '\n' ' ' | sed 's/ $//')
scn_assert_eq 2.only_declared_names "" "$undeclared"
scn_assert_eq 2.has_path yes "$(printf '%s\n' $names | grep -qx PATH && echo yes || echo no)"

scn_check "3. stopping the gateway shuts the child down gracefully"
stdio_stop_gateway || scn_fail 3.gateway_stopped "the gateway did not exit within 30 s of SIGTERM"
left=""
for _ in $(seq 1 75); do # the shutdown ladder takes at most 5 s + 5 s
  left=$(stdio_child_pids)
  [ -z "$left" ] && break
  sleep 0.2
done
scn_assert_eq 3.no_child_left "" "$left"
scn_assert_eq 3.graceful_marker yes "$([ -f "$STDIO_MARKER" ] && echo yes || echo "no marker at $STDIO_MARKER")"

scn_pass
