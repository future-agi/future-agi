#!/usr/bin/env bash
# Stubbed Windows netstat output for bin/lib/ports.sh; no host ports are opened.
# The bash -c scripts expand their arguments in the child shell.
# shellcheck disable=SC2016
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT="$PWD"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# lsof and ss are deliberately absent so the netstat branch is taken.
STUB="$TMP/stubbin"
mkdir -p "$STUB"
for tool in awk bash grep sed tail cut uname; do
  ln -s "$(command -v "$tool")" "$STUB/$tool"
done
cat >"$STUB/netstat" <<'EOF'
#!/usr/bin/env bash
[[ "$*" == '-ano' ]] || exit 1
# CRLF is the native Windows line ending. Non-listening rows come first so
# checking only the local port would pick the wrong PID.
printf '%s\r\n' \
  'Active Connections' \
  '  Proto  Local Address          Foreign Address        State           PID' \
  '  TCP    127.0.0.1:3000        127.0.0.1:80           ESTABLISHED     9991' \
  '  TCP    127.0.0.1:3000        127.0.0.1:800          TIME_WAIT       9992' \
  '  TCP    0.0.0.0:30001         0.0.0.0:0              LISTENING       7777' \
  '  TCP    0.0.0.0:3000          0.0.0.0:0              LISTENING       44576' \
  '  TCP    [::]:5432             [::]:0                 LISTENING       1234' \
  '  TCP    127.0.0.1:3001        0.0.0.0:0              ABHÖREN         44577' \
  '  TCP    [::1]:3002            [::]:0                 ÉCOUTE          44578' \
  '  TCP    0.0.0.0:3003          0.0.0.0:0              侦听            44579' \
  '  TCP    127.0.0.1:5000        127.0.0.1:80           ESTABLISHED     9993' \
  '  TCP    [::1]:5001            [::1]:443              TIME_WAIT       9994' \
  '  UDP    0.0.0.0:4000          *:*                                    9999'
EOF
chmod +x "$STUB/netstat"

run_holder() { # run_holder <port> -> stdout of port_holder, always exit 0
  env -i PATH="$STUB" bash -c 'source "$1/bin/lib/ports.sh"; port_holder "$2"' - "$ROOT" "$1"
}

PASS=0
FAIL=0
check() { # check <desc> <expected> <actual>
  local desc="$1" expected="$2" actual="$3"
  if [[ "$actual" == "$expected" ]]; then
    PASS=$((PASS + 1))
    echo "ok - $desc"
  else
    FAIL=$((FAIL + 1))
    echo "not ok - $desc"
    echo "  expected: [$expected]"
    echo "  got: [$actual]"
  fi
}

# Compare the PID rather than localized text or whitespace.
holder_pid() {
  run_holder "$1" | awk '{gsub(/\r/, ""); print $NF}'
}
check "detects occupied TCP port 3000, not :30001 or a connected row" "44576" "$(holder_pid 3000)"
check "detects IPv6 TCP listener" "1234" "$(holder_pid 5432)"
check "detects German loopback listener" "44577" "$(holder_pid 3001)"
check "detects French IPv6 loopback listener" "44578" "$(holder_pid 3002)"
check "detects Chinese listener" "44579" "$(holder_pid 3003)"
check "matches exact port 30001" "7777" "$(holder_pid 30001)"
check "reports unoccupied port as free" "" "$(run_holder 3999)"
check "ignores UDP-only port" "" "$(run_holder 4000)"
check "ignores established TCP connection" "" "$(run_holder 5000)"
check "ignores TCP time-wait connection" "" "$(run_holder 5001)"
check "does not match a port suffix" "" "$(run_holder 300)"

# Exercise the actual preflight and .env writer, not just the row parser.
# All four adjacent occupied ports must be skipped before saving 3004.
printf 'FRONTEND_PORT=3000\n' >"$TMP/.env"
out=$(env -i PATH="$STUB" bash -c '
  cd "$2"
  source "$1/bin/lib/env.sh"
  source "$1/bin/lib/ports.sh"
  DIM="" RESET=""
  ok() { printf "%s\n" "$*"; }
  warn() { printf "%s\n" "$*"; }
  say() { printf "%s\n" "$*"; }
  docker() { return 0; } # No containers already own these ports.
  check_host_ports 0 example 1
  printf "SAVED=%s" "$(env_value FRONTEND_PORT "")"
' - "$ROOT" "$TMP")
check "preflight saves the next free frontend port" "SAVED=3004" "${out##*$'\n'}"
if [[ "${PORT_TEST_EVIDENCE:-}" == 1 ]]; then
  printf '\n--- Actual preflight output (stubbed Windows netstat) ---\n%s\n' "$out"
fi

# Existing platform choices keep precedence, even when they report no holder.
cat >"$STUB/ss" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' 'LISTEN 0 128 127.0.0.1:3000 0.0.0.0:*'
EOF
chmod +x "$STUB/ss"
check "ss takes precedence over netstat" "LISTEN 0 128 127.0.0.1:3000 0.0.0.0:*" "$(run_holder 3000)"
cat >"$STUB/lsof" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' 'COMMAND PID USER' 'node 42 test'
EOF
chmod +x "$STUB/lsof"
check "lsof takes precedence over ss and netstat" "node (pid 42)" "$(run_holder 3000)"
printf '#!/usr/bin/env bash\nexit 1\n' >"$STUB/lsof"
check "empty lsof result stays free" "" "$(run_holder 3000)"
rm "$STUB/lsof" "$STUB/ss"
printf '#!/usr/bin/env bash\nexit 1\n' >"$STUB/netstat"
check "empty netstat result stays free" "" "$(run_holder 3000)"

printf '%s\n' '---' "$PASS pass, $FAIL fail"
[[ "$FAIL" -eq 0 ]]
