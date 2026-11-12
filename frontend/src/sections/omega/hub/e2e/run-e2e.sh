#!/usr/bin/env bash
# Runs the Hub e2e against a real local backend (scripts/omega-hub/dev-up.sh
# with OMEGA_HUB_PORT=8741, OMEGA_HUB_LANE_STATE=/tmp/hubfe-lane).
#   PLAYWRIGHT_NODE_MODULES  a node_modules holding playwright whose version
#                            matches the browsers in PLAYWRIGHT_BROWSERS_PATH
#   HUB_E2E_WORK             scratch dir for the build, results and the home dir
# Extra arguments go to `playwright test`.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND="$(cd "$HERE/../../../../.." && pwd)"
: "${PLAYWRIGHT_NODE_MODULES:?set PLAYWRIGHT_NODE_MODULES to a node_modules with playwright}"
: "${PLAYWRIGHT_BROWSERS_PATH:=$HOME/Library/Caches/ms-playwright}"
: "${HUB_E2E_WORK:=${TMPDIR:-/tmp}/hubfe-e2e}"
: "${OMEGA_HUB_LANE_STATE:=/tmp/hubfe-lane}"
: "${HUB_SCREENS_DIR:=$HOME/.futureagi-local/release-tabs/screens/hub-frontend}"

mkdir -p "$HUB_E2E_WORK/home" "$HUB_E2E_WORK/xdg" "$HUB_SCREENS_DIR"
export PLAYWRIGHT_BROWSERS_PATH HUB_E2E_WORK OMEGA_HUB_LANE_STATE HUB_SCREENS_DIR
export NODE_PATH="$PLAYWRIGHT_NODE_MODULES"
# Spawned tools never see the real home or git config.
export HOME="$HUB_E2E_WORK/home"
export XDG_CONFIG_HOME="$HUB_E2E_WORK/xdg" XDG_CACHE_HOME="$HUB_E2E_WORK/xdg"
export GIT_CONFIG_GLOBAL=/dev/null

cd "$FRONTEND"
exec node "$PLAYWRIGHT_NODE_MODULES/playwright/cli.js" test -c "$HERE/playwright.config.cjs" "$@"
