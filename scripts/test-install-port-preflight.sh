#!/usr/bin/env bash
# Regression for github.com/future-agi/future-agi/issues/2341
#
# bin/install's host-port preflight must include fi-collector OTLP/admin
# (default stack) and Temporal UI (observability/full profiles only).
# Otherwise a collision on those ports fails as a raw docker compose
# "address already in use" instead of the friendly auto-resolve.
#
# No Docker. Extracts the PORTS_TO_CHECK construction from bin/install
# and evals it under light / --full / observability-profile .env.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL="$ROOT/bin/install"
COMPOSE="$ROOT/docker-compose.yml"

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf '  ok  %s\n' "$*"; }

[[ -f "$INSTALL" ]] || fail "missing $INSTALL"
[[ -f "$COMPOSE" ]] || fail "missing $COMPOSE"

python3 - "$INSTALL" "$COMPOSE" << 'PY'
import re, sys
from pathlib import Path

install = Path(sys.argv[1]).read_text()
compose = Path(sys.argv[2]).read_text()

expected = {
    "FI_COLLECTOR_OTLP_PORT": "4317",
    "FI_COLLECTOR_OTLP_HTTP_PORT": "4318",
    "FI_COLLECTOR_ADMIN_PORT": "9464",
    "TEMPORAL_UI_PORT": "8085",
}

for var, default in expected.items():
    pat = r"\$\{" + var + r":-" + default + r"\}"
    if not re.search(pat, compose):
        raise SystemExit(f"docker-compose.yml missing ${{{var}:-{default}}}")

def service_block(text, name):
    m = re.search(
        rf"^  {re.escape(name)}:\n(?:[ ]{{4,}}.*\n|[ \t]*\n)*",
        text,
        re.M,
    )
    if not m:
        raise SystemExit(f"could not parse {name} service")
    return m.group(0)

ui_block = service_block(compose, "temporal-ui")
col_block = service_block(compose, "fi-collector")
if "observability" not in ui_block or "full" not in ui_block:
    raise SystemExit("temporal-ui is not gated on observability/full profiles")
if "profiles:" in col_block:
    raise SystemExit("fi-collector unexpectedly has a compose profile")
PY

extract_ports_block() {
  python3 - "$INSTALL" << 'PY'
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text()
start = text.find("declare -a PORTS_TO_CHECK=(")
if start < 0:
    raise SystemExit("PORTS_TO_CHECK not found")
end = text.find("declare -a UNRESOLVED=", start)
if end < 0:
    raise SystemExit("UNRESOLVED sentinel not found")
sys.stdout.write(text[start:end])
PY
}

has_entry() {
  local needle="$1" e
  for e in "${PORTS_TO_CHECK[@]}"; do
    [[ "$e" == "$needle" ]] && return 0
  done
  return 1
}

assert_has() {
  has_entry "$1" || fail "$2: missing $1  (got: ${PORTS_TO_CHECK[*]})"
  pass "$2 has $1"
}

assert_missing() {
  if has_entry "$1"; then
    fail "$2: unexpected $1  (got: ${PORTS_TO_CHECK[*]})"
  fi
  pass "$2 omits $1"
}

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/fagi-2341.XXXXXX")"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT
cd "$WORKDIR"

echo "==> PORTS_TO_CHECK construction (#2341)"

printf '%s\n' "COMPOSE_PROFILES=" > .env
FULL=0
unset PORTS_TO_CHECK || true
declare -a PORTS_TO_CHECK=()
eval "$(extract_ports_block)"
assert_has "FI_COLLECTOR_OTLP_PORT:4317" "light"
assert_has "FI_COLLECTOR_OTLP_HTTP_PORT:4318" "light"
assert_has "FI_COLLECTOR_ADMIN_PORT:9464" "light"
assert_has "TEMPORAL_PORT:7233" "light"
assert_missing "TEMPORAL_UI_PORT:8085" "light"
assert_missing "PEERDB_UI_PORT:3001" "light"
assert_missing "PEERDB_PORT:9900" "light"

printf '%s\n' "COMPOSE_PROFILES=" > .env
FULL=1
unset PORTS_TO_CHECK || true
declare -a PORTS_TO_CHECK=()
eval "$(extract_ports_block)"
assert_has "FI_COLLECTOR_OTLP_PORT:4317" "--full"
assert_has "FI_COLLECTOR_OTLP_HTTP_PORT:4318" "--full"
assert_has "FI_COLLECTOR_ADMIN_PORT:9464" "--full"
assert_has "TEMPORAL_UI_PORT:8085" "--full"
assert_has "PEERDB_UI_PORT:3001" "--full"
assert_has "PEERDB_PORT:9900" "--full"

printf '%s\n' "COMPOSE_PROFILES=observability" > .env
FULL=0
unset PORTS_TO_CHECK || true
declare -a PORTS_TO_CHECK=()
eval "$(extract_ports_block)"
assert_has "TEMPORAL_UI_PORT:8085" "observability profile"
assert_missing "PEERDB_UI_PORT:3001" "observability profile"
assert_missing "PEERDB_PORT:9900" "observability profile"

printf '%s\n' "COMPOSE_PROFILES=full" > .env
FULL=0
unset PORTS_TO_CHECK || true
declare -a PORTS_TO_CHECK=()
eval "$(extract_ports_block)"
assert_has "TEMPORAL_UI_PORT:8085" "COMPOSE_PROFILES=full without --full"
assert_missing "PEERDB_UI_PORT:3001" "COMPOSE_PROFILES=full without --full"

printf '%s\n' "# COMPOSE_PROFILES=observability" > .env
FULL=0
unset PORTS_TO_CHECK || true
declare -a PORTS_TO_CHECK=()
eval "$(extract_ports_block)"
assert_missing "TEMPORAL_UI_PORT:8085" "commented COMPOSE_PROFILES"

printf '%s\n' "COMPOSE_PROFILES=observability,full" > .env
FULL=0
unset PORTS_TO_CHECK || true
declare -a PORTS_TO_CHECK=()
eval "$(extract_ports_block)"
assert_has "TEMPORAL_UI_PORT:8085" "comma-separated profiles"

python3 - "$INSTALL" << 'PY'
import re, sys
from pathlib import Path
install = Path(sys.argv[1]).read_text()
start = install.find("declare -a PORTS_TO_CHECK=(")
end = install.find("declare -a UNRESOLVED=", start)
block = install[start:end]
vars_ = set(re.findall(r'"([A-Z0-9_]+):\d+"', block))
need = {
    "FI_COLLECTOR_OTLP_PORT",
    "FI_COLLECTOR_OTLP_HTTP_PORT",
    "FI_COLLECTOR_ADMIN_PORT",
    "TEMPORAL_UI_PORT",
}
missing = need - vars_
if missing:
    raise SystemExit(f"PORTS_TO_CHECK missing vars: {sorted(missing)}")
PY

echo
echo "PASS"
