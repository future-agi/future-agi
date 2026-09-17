#!/usr/bin/env bash
# Regression for github.com/future-agi/future-agi/issues/2340
#
# When bin/install auto-switches BACKEND_PORT, VITE_HOST_API must follow
# if it is empty/unset (the .env.example default) or already a localhost
# URL. Custom/split-domain URLs must be left alone.
#
# No Docker. Extracts sed_inplace / set_env_var / sync_vite_host_api from
# bin/install so this tests the production functions.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL="$ROOT/bin/install"

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf '  ok  %s\n' "$*"; }

[[ -f "$INSTALL" ]] || fail "missing $INSTALL"

grep -q 'sync_vite_host_api' "$INSTALL" \
  || fail "bin/install does not call sync_vite_host_api"

# The apply-block must call sync_vite_host_api only when var is BACKEND_PORT.
python3 -c '
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text()
needle = "if [[ \"$var\" == \"BACKEND_PORT\" ]]; then"
if needle not in text:
    raise SystemExit("missing BACKEND_PORT guard around sync_vite_host_api")
idx = text.index(needle)
window = text[idx:idx + 400]
if "sync_vite_host_api" not in window:
    raise SystemExit("sync_vite_host_api not called under BACKEND_PORT guard")
' "$INSTALL" || fail "BACKEND_PORT call site is missing or too broad"

extract_fn() {
  local name="$1"
  python3 -c '
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text()
name = sys.argv[2]
sig = f"{name}() {{"
start = text.find(sig)
if start == -1:
    raise SystemExit(f"function {name}() not found")
i = start + len(sig) - 1
depth = 0
for j in range(i, len(text)):
    ch = text[j]
    if ch == "{":
        depth += 1
    elif ch == "}":
        depth -= 1
        if depth == 0:
            sys.stdout.write(text[start : j + 1] + "\n")
            break
else:
    raise SystemExit(f"unterminated function {name}")
' "$INSTALL" "$name"
}

eval "$(extract_fn sed_inplace)"
eval "$(extract_fn set_env_var)"
eval "$(extract_fn sync_vite_host_api)"

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/fagi-2340.XXXXXX")"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT
cd "$WORKDIR"

read_vite() {
  if grep -Eq '^VITE_HOST_API=' .env; then
    grep -E '^VITE_HOST_API=' .env | tail -1 | cut -d= -f2-
  else
    printf '<unset>'
  fi
}

run_case() {
  local name="$1" env_body="$2" expect="$3" expect_stdout="$4"
  printf '%s\n' "$env_body" > .env
  local out
  out="$(sync_vite_host_api 8001 || true)"
  local got
  got="$(read_vite)"
  [[ "$got" == "$expect" ]] || fail "$name: VITE_HOST_API='$got' want '$expect'"
  [[ "$out" == "$expect_stdout" ]] || fail "$name: stdout='$out' want '$expect_stdout'"
  pass "$name"
}

echo "==> sync_vite_host_api (#2340)"

run_case "empty value (env.example default)" \
  $'BACKEND_PORT=8000\nVITE_HOST_API=\nFRONTEND_URL=' \
  "http://localhost:8001" "http://localhost:8001"

run_case "unset key" \
  $'BACKEND_PORT=8000\nFRONTEND_URL=' \
  "http://localhost:8001" "http://localhost:8001"

run_case "quoted empty double" \
  $'VITE_HOST_API=""' \
  "http://localhost:8001" "http://localhost:8001"

run_case "quoted empty single" \
  "VITE_HOST_API=''" \
  "http://localhost:8001" "http://localhost:8001"

run_case "http localhost" \
  "VITE_HOST_API=http://localhost:8000" \
  "http://localhost:8001" "http://localhost:8001"

run_case "https localhost" \
  "VITE_HOST_API=https://localhost:8000" \
  "http://localhost:8001" "http://localhost:8001"

run_case "trailing slash" \
  "VITE_HOST_API=http://localhost:8000/" \
  "http://localhost:8001" "http://localhost:8001"

run_case "quoted http localhost" \
  'VITE_HOST_API="http://localhost:8000"' \
  "http://localhost:8001" "http://localhost:8001"

run_case "quoted https localhost trailing slash" \
  'VITE_HOST_API="https://localhost:8000/"' \
  "http://localhost:8001" "http://localhost:8001"

run_case "CRLF empty value" \
  $'VITE_HOST_API=\r' \
  "http://localhost:8001" "http://localhost:8001"

run_case "custom https API left alone" \
  "VITE_HOST_API=https://api.example.com" \
  "https://api.example.com" ""

run_case "quoted custom API left alone" \
  'VITE_HOST_API="https://api.example.com"' \
  '"https://api.example.com"' ""

run_case "localhost with path left alone" \
  "VITE_HOST_API=http://localhost:8000/api" \
  "http://localhost:8000/api" ""

run_case "127.0.0.1 left alone (not in installer defaults)" \
  "VITE_HOST_API=http://127.0.0.1:8000" \
  "http://127.0.0.1:8000" ""

echo
echo "PASS"
