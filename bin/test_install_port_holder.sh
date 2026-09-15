#!/usr/bin/env bash
# Regression tests for port_holder() in bin/install.
# Covers the Windows netstat fallback (future-agi#2274, fixed by #2396):
# with lsof/ss unavailable, TCP LISTENING ports must still be detected.
set -uo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Extract port_holder() without running the installer.
sed -n '/^port_holder() {$/,/^}$/p' bin/install >"$TMP/port_holder.sh"
if [[ ! -s "$TMP/port_holder.sh" ]]; then
  echo "FAIL: could not extract port_holder() from bin/install"
  exit 1
fi

# Stub bin: a fake Windows-style netstat plus the tools port_holder needs.
# lsof and ss are deliberately absent so the netstat branch is taken.
STUB="$TMP/stubbin"
mkdir -p "$STUB"
ln -s "$(command -v awk)" "$STUB/awk"
ln -s "$(command -v head)" "$STUB/head"
ln -s "$(command -v bash)" "$STUB/bash"
cat >"$STUB/netstat" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' \
  'Active Connections' \
  '  Proto  Local Address          Foreign Address        State           PID' \
  '  TCP    0.0.0.0:3000           0.0.0.0:0              LISTENING       44576' \
  '  TCP    [::]:5432              [::]:0                 LISTENING       1234' \
  '  TCP    0.0.0.0:30001          0.0.0.0:0              LISTENING       7777' \
  '  UDP    0.0.0.0:4000           *:*                                    9999'
EOF
chmod +x "$STUB/netstat"

run_holder() { # run_holder <port> -> stdout of port_holder, always exit 0
  env -i PATH="$STUB" bash -c "source '$TMP/port_holder.sh'; port_holder '$1'"
}

PASS=0
FAIL=0
check() { # check <desc> <expected-substring-or-empty> <actual>
  local desc="$1" expected="$2" actual="$3"
  if [[ -z "$expected" && -z "$actual" ]]; then
    PASS=$((PASS + 1)); echo "ok - $desc"
  elif [[ -n "$expected" && "$actual" == *"$expected"* ]]; then
    PASS=$((PASS + 1)); echo "ok - $desc"
  else
    FAIL=$((FAIL + 1)); echo "not ok - $desc"
    echo "  expected to contain: [$expected]"
    echo "  got: [$actual]"
  fi
}

out=$(run_holder 3000)
check "detects occupied TCP port 3000" "44576" "$out"

out=$(run_holder 5432)
check "detects IPv6 TCP listener" "1234" "$out"

out=$(run_holder 3999)
check "reports unoccupied port as free" "" "$out"

out=$(run_holder 4000)
check "ignores UDP-only port" "" "$out"

out=$(run_holder 30001)
check "matches exact port 30001" "7777" "$out"

out=$(run_holder 3000)
if [[ "$out" == *"7777"* ]]; then
  FAIL=$((FAIL + 1)); echo "not ok - port 3000 must not match :30001"
else
  PASS=$((PASS + 1)); echo "ok - port 3000 must not match :30001"
fi

echo "---"
echo "passed: $PASS, failed: $FAIL"
[[ "$FAIL" -eq 0 ]]
