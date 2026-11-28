#!/bin/bash
# Usage (from e2e/, on the stack host):
#   demo/run.sh setup  <take> <email-domain>              fresh synthetic org through public APIs
#   demo/run.sh record <take> [fast=0|1] [chapters]       record the walkthrough for that org
#   demo/run.sh all    <take> <email-domain> [fast] [chapters]
# The stack is reached through the public origin (demo/public-origin.mjs); E2E_APP_URL defaults to it.
set -euo pipefail
step="$1"; take="$2"; out="$HOME/demo-out/$take"
export E2E_APP_URL="${E2E_APP_URL:-https://app.${DEMO_PUBLIC_DOMAIN:-acme-support.example}}"
setup() { rm -rf "$out"; mkdir -p "$out"; node demo/setup.mjs "$out/state.json" "$1" > "$out/setup.log" 2>&1 || { tail -20 "$out/setup.log"; exit 1; }; echo "SETUP_OK $out"; }
record() {
  local fast="${1:-0}" chapters="${2:-1,2,3,4,5,6,7,8}"
  rm -rf "$out/shots" "$out/video" "$out/events.json"
  DEMO_STATE="$out/state.json" DEMO_OUT="$out" DEMO_FAST="$fast" DEMO_CHAPTERS="$chapters" \
    npx playwright test -c demo/playwright.demo.config.ts walkthrough > "$out/run.log" 2>&1 && code=0 || code=$?
  tail -40 "$out/run.log"; echo "EXIT=$code"
}
case "$step" in
  setup) setup "${3:?email domain}";;
  record) record "${3:-0}" "${4:-}";;
  all) setup "${3:?email domain}"; record "${4:-0}" "${5:-}";;
  *) sed -n 2,6p "$0"; exit 2;;
esac
