#!/usr/bin/env bash
# Runs the Studio tab e2e in Chromium, Firefox and WebKit.
#   PLAYWRIGHT_NODE_MODULES  a node_modules holding playwright (1.60, matching
#                            the browsers in PLAYWRIGHT_BROWSERS_PATH)
#   STUDIO_OMEGA_REPO        an Omega checkout with packages/cli/dist built from
#                            the accepted studio-embed head (default:
#                            <worktree>/.omega/build/omega-studio-embed)
#   STUDIO_E2E_WORK          scratch dir for the build, results and evidence
#   STUDIO_SCREENS_DIR       where screenshots go
# Extra arguments go to `playwright test` (e.g. --project=chromium).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND="$(cd "$HERE/../../../../.." && pwd)"
: "${PLAYWRIGHT_NODE_MODULES:?set PLAYWRIGHT_NODE_MODULES to a node_modules with playwright}"
: "${PLAYWRIGHT_BROWSERS_PATH:=$HOME/Library/Caches/ms-playwright}"
: "${STUDIO_E2E_WORK:=${TMPDIR:-/tmp}/omega-studio-tab-e2e}"
: "${STUDIO_SCREENS_DIR:=$STUDIO_E2E_WORK/screens}"

mkdir -p "$STUDIO_E2E_WORK/home" "$STUDIO_E2E_WORK/xdg" "$STUDIO_SCREENS_DIR/engines"
export PLAYWRIGHT_BROWSERS_PATH STUDIO_E2E_WORK STUDIO_SCREENS_DIR
export NODE_PATH="$PLAYWRIGHT_NODE_MODULES"
# Spawned tools never see the real home or git config.
export HOME="$STUDIO_E2E_WORK/home"
export XDG_CONFIG_HOME="$STUDIO_E2E_WORK/xdg" XDG_CACHE_HOME="$STUDIO_E2E_WORK/xdg"
export GIT_CONFIG_GLOBAL=/dev/null

cd "$FRONTEND"
exec node "$PLAYWRIGHT_NODE_MODULES/playwright/cli.js" test \
  -c "$HERE/playwright.config.cjs" "$@"
