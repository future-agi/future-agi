#!/usr/bin/env bash
# omega-ee-attach/v1: check the golden fixtures, the spec that cites them, and
# the code they copy. Spec: docs/gateway/connectors/omega-ee-attach.md.
#
#   bash agentcc-gateway/scripts/scenarios/fixtures/omega-ee-attach/check.sh
#
# It fails when:
#   - a fixture does not parse or does not validate against its schema.json
#     entry, or a forbidden/ fixture is not refused for its stated reason;
#   - a cross-fixture check, SHA256SUMS or the spec's "Set digest" line fails;
#   - a gateway or Django constant the fixtures copy has changed;
#   - the typed-result names in the spec's marked table differ from the names
#     on MCP-PLAN.md lines 1179-1180;
#   - secrets-lint finds a credential-shaped value in a fixture or the spec.
#
# Environment:
#   MCP_PLAN   the plan file (default ~/.futureagi-local/gateway-program/MCP-PLAN.md,
#              the gateway program's copy, outside this repository). Missing is a
#              failure, so no run skips the comparison by accident. In CI, or
#              anywhere without that file, set MCP_PLAN=none: the names then come
#              from the two plan lines vendored in typed-results.json, which must
#              still hash to the pinned sha256, and the output says so.
#
#     CI:  MCP_PLAN=none bash agentcc-gateway/scripts/scenarios/fixtures/omega-ee-attach/check.sh
#   OMEGA_EE_ATTACH_JSONSCHEMA_PYTHON
#              a Python with the jsonschema package: it must agree with the
#              bundled validator on every fixture.
#
# Reads only files; makes no network call and starts nothing.
set -euo pipefail

DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
GW=$(cd "$DIR/../../../.." && pwd)
ROOT=$(cd "$GW/.." && pwd)
PLAN=${MCP_PLAN:-$HOME/.futureagi-local/gateway-program/MCP-PLAN.md}

args=(--repo "$ROOT" --plan "$PLAN")
if [ -n "${OMEGA_EE_ATTACH_JSONSCHEMA_PYTHON:-}" ]; then
  args+=(--reference-python "$OMEGA_EE_ATTACH_JSONSCHEMA_PYTHON")
fi
status=0
python3 -I "$DIR/validate.py" "${args[@]}" || status=1

# secrets-lint runs whatever the validator said, so one run reports both.
files=("$ROOT/docs/gateway/connectors/omega-ee-attach.md")
while IFS= read -r f; do
  files+=("$f")
done < <(find "$DIR" -type f -name '*.json' | LC_ALL=C sort)
if bash "$GW/scripts/release/secrets-lint.sh" "${files[@]}"; then
  echo "ok   secrets-lint: no credential-shaped value in ${#files[@]} files"
else
  echo "FAIL secrets-lint found a credential-shaped value (fixtures hold {{PLACEHOLDER}} tokens only)"
  status=1
fi
if [ "$status" -ne 0 ]; then
  echo "FAILED: omega-ee-attach/v1"
  exit 1
fi
echo "PASS: omega-ee-attach/v1"
