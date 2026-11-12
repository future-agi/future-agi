#!/usr/bin/env bash
# Runs the console-frame e2e (Chromium, Firefox, WebKit).
#   PLAYWRIGHT_NODE_MODULES  a node_modules holding playwright whose version
#                            matches the browsers in PLAYWRIGHT_BROWSERS_PATH
#   CONSOLE_E2E_WORK         scratch dir for the build, results and screenshots
#   CONSOLE_E2E_EE_DIST      optional by default; required for --full-stack:
#                            the EE Console's embed build (dist-embed)
#   CONSOLE_E2E_EE_FIXTURES  optional by default; required for --full-stack:
#                            the EE lane's e2e/embed/fixtures.ts
#   CONSOLE_SCREENS_DIR      where screens.e2e.cjs writes its screenshots
# Default stand-in lifecycle: ./run-e2e.sh --project=chromium
# Strict real-EE lifecycle (missing EE build FAILS, never skips):
#   CONSOLE_E2E_EE_DIST=/path/to/omega-ee/apps/console/dist-embed \
#   CONSOLE_E2E_EE_FIXTURES=/path/to/omega-ee/apps/console/e2e/embed/fixtures.ts \
#   ./run-e2e.sh --full-stack --project=chromium-full-stack
# --full-stack must be first; it selects playwright.full-stack.config.cjs.
# All remaining arguments go to `playwright test`.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$HERE/playwright.config.cjs"
if [[ "${1:-}" == "--full-stack" ]]; then
  CONFIG="$HERE/playwright.full-stack.config.cjs"
  shift
fi
FRONTEND="$(cd "$HERE/../../../../.." && pwd)"
: "${PLAYWRIGHT_NODE_MODULES:?set PLAYWRIGHT_NODE_MODULES to a node_modules with playwright}"
: "${PLAYWRIGHT_BROWSERS_PATH:=$HOME/Library/Caches/ms-playwright}"
: "${CONSOLE_E2E_WORK:=${TMPDIR:-/tmp}/omega-console-frame-e2e}"

mkdir -p "$CONSOLE_E2E_WORK/home" "$CONSOLE_E2E_WORK/xdg"
export PLAYWRIGHT_BROWSERS_PATH CONSOLE_E2E_WORK
export NODE_PATH="$PLAYWRIGHT_NODE_MODULES"
# Spawned tools never see the real home or git config.
export HOME="$CONSOLE_E2E_WORK/home"
export XDG_CONFIG_HOME="$CONSOLE_E2E_WORK/xdg" XDG_CACHE_HOME="$CONSOLE_E2E_WORK/xdg"
export GIT_CONFIG_GLOBAL=/dev/null

cd "$FRONTEND"
exec node "$PLAYWRIGHT_NODE_MODULES/playwright/cli.js" test \
  -c "$CONFIG" "$@"
