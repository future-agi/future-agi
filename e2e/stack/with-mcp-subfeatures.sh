#!/usr/bin/env bash
# Per-walk deployment switches for the Standalone MCP stack (708 add.42).
# Run EVERY stack command and the browser process under the same selection:
#   bash e2e/stack/with-mcp-subfeatures.sh W07 bin/e2e up
#   bash e2e/stack/with-mcp-subfeatures.sh W07 bash -c \
#     'cd e2e && yarn playwright test --config playwright.walk.config.ts --grep @walk-W07'
# This wraps the existing profiles; it adds no profile name to lib/profile.ts.
# W07 and W08 both use mcp, but MUST run as separate stack phases. Do not run
# the combined W07-W10 grep under a single selection. Use the same wrapper for
# compose/inspect/down too, so the inspector sees exactly the stack's flags.
# The harness at gw/E2E-MCP-HARNESS-FIX-1 / 0754a8d3bf supplies the mock command
# switch: W07/W09 use its static bearer; W08 uses E2E_MCP_MOCK_AUTH=oauth.
set -euo pipefail

usage() {
  echo 'usage: with-mcp-subfeatures.sh W07|W08|W09|W10|W12|W14|mcp|mcp-upstream|mcp-signin|mcp-catalog|connectors-browse <command> [args...]' >&2
}
if (( $# < 2 )); then usage; exit 64; fi
selection=$1
shift

# The reviewed table is shared with Compose and the inspector. As before, this
# explicit wrapper selection replaces stale inherited switches; bin/e2e and the
# inspector refuse any subsequent mismatch rather than silently normalizing it.
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
if ! declaration=$(node "$root/e2e/stack/mcp-subfeature-selection.mjs" --select "$selection"); then
  usage; exit 64
fi
while IFS='=' read -r key value; do
  export "$key=$value"
done <<<"$declaration"
exec "$@"
