# shellcheck shell=bash
# The K2 replay of a catalog's rest providers: 94_cn_rest_runtime.sh's mode
# for `--catalog <dir> --apps <app,...>|all` (IR-CN14c-1, IR-CN15c-1, review
# R-CN14c-1 and R-CN15c-1). It folds in CN14c's and CN15c's lane replays
# (Gmail and Google Calendar; Outlook and Teams) so each provider's committed
# cassette replays in the repository's gate. Real binaries, loopback only:
#  0. each app's committed cassette (fixtures/cassettes/<app>*.cassette.json
#     under its kind rest provider) passes agentcc-cassette check and names
#     the test account its expectations pin; one replay serves them all,
#     behind k2drive's witness; agentcc-connectors starts on --catalog-dir
#     <dir>, with every x-origin of the apps' generated tools replayed, and
#     /healthz names each app;
#  1. a request without the user's token gets 401 with WWW-Authenticate, and
#     the vendor gets nothing;
#  2. the gateway's MCP client (k2drive drive, M13), pinned to 2025-11-25 and
#     to 2026-07-28, lists each app's tools and makes every call of its
#     committed <app>.calls.json with a per-call bearer alternating two
#     users: the vendor stand-in sees exactly that call's token on each
#     request; every call but the last succeeds, and the last is the vendor's
#     404, an attributed tool error; no generator keyword and no $-named
#     input is listed; then the app's own expectations
#     (fixtures/K2-replay/<app>.json: tool count, annotations, projections,
#     wire names);
#  4. the replay answered every request from the recording (exit 0, no miss),
#     and the runtime drains and exits 0 on SIGTERM;
#  5. no token appears in any log or evidence file.
# CN10's step 3 (the real gateway calling the runtime as a YAML MCP server)
# stays with 94's no-argument run: it needs CN10's gateway.yaml fixture and
# proves the runtime-gateway seam once for every app.
#
# `--apps all` takes every app whose kind rest provider has a committed
# cassette, and fails when there is none, or when one of them has no
# expectations file: a recorded rest provider cannot leave the gate.
# Evidence lands in $SCN_HOME/scn-cn10/94_cn_rest_runtime-k2/.

# k2_usage <message>: a usage error, exit 2 (as 93 and 94 do).
k2_usage() { echo "94_cn_rest_runtime.sh: $*" >&2; exit 2; }

# k2_rest_provider <catalog> <app>: the app's kind rest provider id (one).
k2_rest_provider() {
  local y found=""
  for y in "$1/apps/$2/providers/"*.yaml; do
    [ -f "$y" ] || continue
    if grep -qx 'kind: rest' "$y"; then
      [ -z "$found" ] || { echo "app $2 has more than one kind rest provider" >&2; return 1; }
      found=$(basename "$y" .yaml)
    fi
  done
  [ -n "$found" ] || { echo "app $2 has no kind rest provider in $1" >&2; return 1; }
  echo "$found"
}

# k2_cassette <catalog> <app> <provider>: the one committed cassette.
k2_cassette() {
  local dir=$1/apps/$2/providers/$3/fixtures/cassettes f n=0 out=""
  for f in "$dir/$2"*.cassette.json; do
    [ -f "$f" ] || continue
    n=$((n + 1))
    out=$f
  done
  [ "$n" -eq 1 ] || { echo "app $2/$3 has $n cassettes ($dir/$2*.cassette.json), want 1" >&2; return 1; }
  echo "$out"
}

# k2_all_apps <catalog>: every app whose rest provider has a committed cassette.
k2_all_apps() {
  local y app prov
  for y in "$1"/apps/*/providers/*.yaml; do
    grep -qx 'kind: rest' "$y" || continue
    prov=$(basename "$y" .yaml)
    app=$(basename "$(dirname "$(dirname "$y")")")
    compgen -G "$1/apps/$app/providers/$prov/fixtures/cassettes/$app*.cassette.json" >/dev/null && echo "$app"
  done | sort -u
}

# k2_expectations <catalog> <app> <provider> <legacy-dir>
# The provider owns its proof. Legacy files remain a fallback, never an override.
k2_expectations() {
  local owned=$1/apps/$2/providers/$3/fixtures/replay-expectations.json
  local legacy=$4/$2.json a b
  if [ -f "$owned" ]; then
    a=$(jq -e -S -c 'select(type == "object")' "$owned") || return 1
    if [ -f "$legacy" ]; then
      b=$(jq -e -S -c 'select(type == "object")' "$legacy") || return 1
      [ "$a" = "$b" ] || { echo "app $2 has conflicting app-owned and common replay expectations" >&2; return 1; }
    fi
    printf '%s\n' "$owned"
  elif [ -f "$legacy" ]; then
    jq -e 'type == "object"' "$legacy" >/dev/null || return 1
    printf '%s\n' "$legacy"
  else
    echo "app $2 has a committed K2 cassette but no expectations file" >&2
    return 1
  fi
}

k2_replay_main() { # catalog apps
  local catalog=$1 apps_arg=$2
  case "$catalog" in /*) ;; *) catalog=$SCN_GATEWAY_DIR/$catalog ;; esac
  [ -d "$catalog/apps" ] || k2_usage "--catalog $1: no apps/ directory"
  catalog=$(cd "$catalog" && pwd)
  local expect_dir=$SCN_ROOT/fixtures/K2-replay
  local apps=() app
  if [ "$apps_arg" = all ]; then
    while IFS= read -r app; do apps+=("$app"); done < <(k2_all_apps "$catalog")
    [ ${#apps[@]} -gt 0 ] || k2_usage "--apps all: no kind rest provider in $catalog has a committed cassette"
  else
    IFS=, read -r -a apps <<<"$apps_arg"
  fi

  K2_HOME=$SCN_HOME/scn-cn10/$(basename "$0" .sh)-k2
  local work=$K2_HOME/work evid=$K2_HOME/evidence
  rm -rf "$K2_HOME"
  mkdir -p "$work" "$evid" "$SCN_BIN"
  echo "K2 replay: catalog $catalog, apps ${apps[*]}"

  # Each app (index k): its rest provider, cassette, calls and expectations,
  # in arrays aligned with apps (bash 3.2 has no associative arrays).
  local cas=() calls=() exps=() origins=() o p k
  for ((k = 0; k < ${#apps[@]}; k++)); do
    app=${apps[$k]}
    [[ "$app" =~ ^[a-z][a-z0-9-]*$ ]] || k2_usage "--apps: bad app id '$app'"
    p=$(k2_rest_provider "$catalog" "$app") || scn_fail "k2.$app.provider" "no single kind rest provider"
    cas[$k]=$(k2_cassette "$catalog" "$app" "$p") || scn_fail "k2.$app.cassette" "no single committed cassette"
    calls[$k]=$catalog/apps/$app/providers/$p/fixtures/cassettes/$app.calls.json
    [ -f "${calls[$k]}" ] || scn_fail "k2.$app.calls" "no ${calls[$k]}"
    exps[$k]=$(k2_expectations "$catalog" "$app" "$p" "$expect_dir") || \
      scn_fail "k2.$app.expectations" "missing, invalid or conflicting replay expectations"
    while IFS= read -r o; do
      [[ " ${origins[*]:-} " == *" $o "* ]] || origins+=("$o")
    done < <(jq -r '[.. | objects | select(has("x-origin")) | ."x-origin"] | unique[]' \
      "$catalog/apps/$app/providers/$p/tools.generated.json")
  done
  [ ${#origins[@]} -gt 0 ] || scn_fail k2.origins "the apps' generated tools name no x-origin"

  k2_build() { # binary package (SCN_SKIP_BUILD=1 reuses a built one, as cn10_build does)
    [ -n "${SCN_SKIP_BUILD:-}" ] && [ -x "$SCN_BIN/$1" ] && return 0
    (cd "$SCN_GATEWAY_DIR" && go build -o "$SCN_BIN/$1" "$2") >>"$K2_HOME/build.log" 2>&1 ||
      scn_fail "k2.build.$1" "building $2 failed: $(tail -5 "$K2_HOME/build.log")"
  }
  k2_build agentcc-connectors ./cmd/agentcc-connectors
  k2_build agentcc-cassette ./cmd/agentcc-cassette
  k2_build k2drive ./internal/connectorcatalog/restmcp/k2drive

  # Fresh fake tokens for this run: never a real credential, never in a file
  # this run writes (step 5 checks).
  tok() { python3 -I -c 'import secrets; print("k2-scn-" + secrets.token_hex(16))'; }
  K2_TOKEN_A=$(tok)
  K2_TOKEN_B=$(tok)
  export K2_TOKEN_A K2_TOKEN_B
  sha() { printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1; }

  # k2_start <name> <command...>: a background process (output in
  # $work/<name>.log), stopped on exit with the MCP mocks (lib_mcp.sh).
  k2_start() {
    local name=$1
    shift
    "$@" >"$work/$name.log" 2>&1 &
    K2_PID=$!
    _SCN_MCP_PIDS+=("$K2_PID")
  }
  trap _scn_mcp_down EXIT
  k2_addr() { # name pid
    local deadline=$(($(date +%s) + 15))
    until [ -s "$work/$1.addr" ]; do
      kill -0 "$2" 2>/dev/null || { echo "$1 exited: $(tail -5 "$work/$1.log")" >&2; return 1; }
      [ "$(date +%s)" -ge "$deadline" ] && { echo "$1 wrote no address: $(tail -5 "$work/$1.log")" >&2; return 1; }
      sleep 0.1
    done
    tr -d '\n' <"$work/$1.addr"
  }

  scn_check "0. the cassettes, the replay, the witness and the runtime on $catalog"
  local replay_args=() runtime_args=()
  for ((k = 0; k < ${#apps[@]}; k++)); do replay_args+=(--cassette "${cas[$k]}"); done
  "$SCN_BIN/agentcc-cassette" check "${cas[@]}" >"$work/check.log" 2>&1
  scn_assert_eq 0.cassette_check 0 "$?"
  for ((k = 0; k < ${#apps[@]}; k++)); do
    scn_assert_jq "0.${apps[$k]}.test_account" '.test_account' "$(jq -r .test_account "${exps[$k]}")" "${cas[$k]}"
  done
  k2_start replay "$SCN_BIN/agentcc-cassette" replay "${replay_args[@]}" --addr 127.0.0.1:0 --addr-file "$work/replay.addr"
  local replay_pid=$K2_PID replay
  replay=$(k2_addr replay "$replay_pid") || scn_fail k2.replay "the replay did not start"
  k2_start witness "$SCN_BIN/k2drive" witness --upstream "http://$replay" --addr 127.0.0.1:0 \
    --addr-file "$work/witness.addr" --out "$evid/vendor-requests.jsonl"
  local witness
  witness=$(k2_addr witness "$K2_PID") || scn_fail k2.witness "the witness did not start"
  touch "$evid/vendor-requests.jsonl"
  for o in "${origins[@]}"; do runtime_args+=(--replay "$o=http://$witness"); done
  k2_start runtime "$SCN_BIN/agentcc-connectors" --addr 127.0.0.1:0 --addr-file "$work/runtime.addr" \
    --catalog-dir "$catalog" "${runtime_args[@]}"
  local runtime_pid=$K2_PID rt
  rt=http://$(k2_addr runtime "$runtime_pid") || scn_fail k2.runtime "the runtime did not start"
  curl -sS --max-time 10 "$rt/healthz" >"$evid/healthz.json"
  scn_assert_jq 0.healthz_status '.status' ok "$evid/healthz.json"
  for app in "${apps[@]}"; do
    scn_assert_jq "0.healthz_serves_$app" ".apps | index(\"$app\") != null" true "$evid/healthz.json"
  done

  scn_check "1. no user token: 401 with WWW-Authenticate, nothing sent to the vendor"
  local code
  for app in "${apps[@]}"; do
    code=$(curl -sS --max-time 10 -o "$work/noauth-$app.body" -D "$work/noauth-$app.headers" -w '%{http_code}' \
      -X POST "$rt/$app/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
      -H 'MCP-Protocol-Version: 2025-11-25' --data-binary '{"jsonrpc":"2.0","id":1,"method":"tools/list"}')
    scn_assert_eq "1.$app.status" 401 "$code"
    scn_assert_eq "1.$app.challenge" yes \
      "$(grep -qi "^WWW-Authenticate: Bearer realm=\"$app\"" "$work/noauth-$app.headers" && echo yes || echo no)"
  done
  scn_assert_eq 1.vendor_untouched 0 "$(wc -l <"$evid/vendor-requests.jsonl" | tr -d ' ')"

  scn_check "2. the gateway's MCP client, a per-call bearer on each call, both eras"
  local era n before d v exp i count name filter want calls_total=0
  for era in 2025-11-25 2026-07-28; do
    for ((k = 0; k < ${#apps[@]}; k++)); do
      app=${apps[$k]}
      exp=${exps[$k]}
      n=$(jq length "${calls[$k]}")
      before=$(wc -l <"$evid/vendor-requests.jsonl" | tr -d ' ')
      "$SCN_BIN/k2drive" drive --url "$rt/$app/mcp" --era "$era" --calls "${calls[$k]}" \
        --conn-token-env K2_TOKEN_A --call-token-env K2_TOKEN_A --call-token-env K2_TOKEN_B \
        --out "$evid/drive-$app-$era.json" >"$work/drive-$app-$era.log" 2>&1
      scn_assert_eq "2.$app.$era.drive_exit" 0 "$?"
      d=$evid/drive-$app-$era.json
      scn_assert_jq "2.$app.$era.negotiated" '.era' "$era" "$d"
      scn_assert_jq "2.$app.$era.tools" '.tools | length' "$(jq -r .tools "$exp")" "$d"
      scn_assert_jq "2.$app.$era.calls" '.calls | length' "$n" "$d"
      scn_assert_jq "2.$app.$era.no_client_errors" '[.calls[] | select(.error)] | length' 0 "$d"
      scn_assert_jq "2.$app.$era.successes" "[.calls[0:$((n - 1))][] | select(.result.isError == false)] | length" \
        "$((n - 1))" "$d"
      scn_assert_jq "2.$app.$era.last_is_vendor_404" '.calls[-1].result.isError' true "$d"
      scn_assert_jq "2.$app.$era.vendor_404_attributed" \
        '.calls[-1].result._meta["com.futureagi/vendor"] | [.name, .host, .status] | join(" ")' \
        "$(jq -r .vendor_404 "$exp")" "$d"
      scn_assert_jq "2.$app.$era.no_generator_keys" '[.tools[] | .inputSchema | tostring | test("\"x-")] | any' false "$d"
      scn_assert_jq "2.$app.$era.no_dollar_inputs" \
        '[.tools[] | .inputSchema.properties // {} | keys[] | startswith("$")] | any' false "$d"
      v=$work/vendor-$app-$era.jsonl
      tail -n +"$((before + 1))" "$evid/vendor-requests.jsonl" >"$v"
      scn_assert_eq "2.$app.$era.vendor_requests" "$n" "$(wc -l <"$v" | tr -d ' ')"
      jq -n --arg a "$(sha "Bearer $K2_TOKEN_A")" --arg b "$(sha "Bearer $K2_TOKEN_B")" --slurpfile d "$d" \
        '[$d[0].calls[] | if .token_index == 0 then $a else $b end]' >"$work/want-$app-$era.json"
      scn_assert_eq "2.$app.$era.per_call_bearer" "$(jq -c . "$work/want-$app-$era.json")" \
        "$(jq -sc '[.[].authorization_sha256]' "$v")"
      # The app's own expectations (fixtures/K2-replay/<app>.json).
      count=$(jq '.drive_checks | length' "$exp")
      for ((i = 0; i < count; i++)); do
        name=$(jq -r ".drive_checks[$i].name" "$exp")
        filter=$(jq -r ".drive_checks[$i].jq" "$exp")
        want=$(jq -r ".drive_checks[$i].want" "$exp")
        scn_assert_jq "2.$app.$era.$name" "$filter" "$want" "$d"
      done
      count=$(jq '.vendor_request_checks | length' "$exp")
      for ((i = 0; i < count; i++)); do
        name=$(jq -r ".vendor_request_checks[$i].name" "$exp")
        want=$(jq -r ".vendor_request_checks[$i].want" "$exp")
        scn_assert_eq "2.$app.$era.$name" "$want" \
          "$(jq -sr ".[$(jq -r ".vendor_request_checks[$i].index" "$exp")] | \"\(.method) \(.path)?\(.query // \"\")\"" "$v")"
      done
      count=$(jq '.every_vendor_request | length' "$exp")
      for ((i = 0; i < count; i++)); do
        name=$(jq -r ".every_vendor_request[$i].name" "$exp")
        filter=$(jq -r ".every_vendor_request[$i].jq" "$exp")
        scn_assert_eq "2.$app.$era.$name" "$n" "$(jq -s "[.[] | select($filter)] | length" "$v")"
      done
      calls_total=$((calls_total + n))
    done
  done

  scn_check "4. the replay missed nothing; the runtime drains and exits 0"
  kill -TERM "$replay_pid"
  wait "$replay_pid"
  scn_assert_eq 4.replay_exit_no_miss 0 "$?"
  kill -TERM "$runtime_pid"
  wait "$runtime_pid"
  scn_assert_eq 4.runtime_exit 0 "$?"
  cp "$work/runtime.log" "$evid/runtime.log"
  cp "$work/replay.log" "$evid/replay.log"
  scn_assert_eq 4.runtime_stopped yes "$(grep -q '"msg":"agentcc-connectors stopped"' "$evid/runtime.log" && echo yes || echo no)"
  scn_assert_eq 4.calls_logged "$calls_total" "$(grep -c '"method":"tools/call"' "$evid/runtime.log")"
  scn_assert_eq 4.unauthenticated_logged "${#apps[@]}" "$(grep -c '"outcome":"unauthenticated"' "$evid/runtime.log")"

  scn_check "5. no token in any log or evidence file"
  local leaks=0 t
  for t in "$K2_TOKEN_A" "$K2_TOKEN_B"; do
    leaks=$((leaks + $(grep -rlF -- "$t" "$work" "$evid" 2>/dev/null | wc -l | tr -d ' ')))
  done
  scn_assert_eq 5.no_token_anywhere 0 "$leaks"
  echo "K2 replay: ${#apps[@]} apps (${apps[*]}), $calls_total calls over two eras"
  scn_pass
}
