#!/usr/bin/env bash
# Runs the embed-host e2e in Chromium, Firefox and WebKit.
#   PLAYWRIGHT_NODE_MODULES  a node_modules holding playwright (1.60, matching
#                            the browsers in PLAYWRIGHT_BROWSERS_PATH)
#   EMBED_E2E_WORK           scratch dir for builds, results and screenshots
# Extra arguments go to `playwright test` (e.g. --project=chromium).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND="$(cd "$HERE/../../../../.." && pwd)"
: "${PLAYWRIGHT_NODE_MODULES:?set PLAYWRIGHT_NODE_MODULES to a node_modules with playwright}"
: "${PLAYWRIGHT_BROWSERS_PATH:=$HOME/Library/Caches/ms-playwright}"
: "${EMBED_E2E_WORK:=${TMPDIR:-/tmp}/omega-embed-host-e2e}"

mkdir -p "$EMBED_E2E_WORK/home" "$EMBED_E2E_WORK/xdg"
export PLAYWRIGHT_BROWSERS_PATH EMBED_E2E_WORK
export NODE_PATH="$PLAYWRIGHT_NODE_MODULES"
# Spawned tools never see the real home or git config.
export HOME="$EMBED_E2E_WORK/home"
export XDG_CONFIG_HOME="$EMBED_E2E_WORK/xdg" XDG_CACHE_HOME="$EMBED_E2E_WORK/xdg"
export GIT_CONFIG_GLOBAL=/dev/null

cd "$FRONTEND"
exec node "$PLAYWRIGHT_NODE_MODULES/playwright/cli.js" test \
  -c "$HERE/playwright.config.cjs" "$@"
