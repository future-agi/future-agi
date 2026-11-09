#!/usr/bin/env bash
# W3b wave-gate capability rows (plan section 3, integrator-owned): GE's W3c
# rows (there is no W3c, C163), with V6 deferred to the next wave (the W3b
# record; orchestrator, 2026-10-18 23:00):
#   1. 08_v6_search.sh: DEFERRED with V6 (not in this wave).
#   2. W3b_capability_ledger_live.sh: a managed capability call reaches D3's
#      drain as one ledger spend entry with provider=<vendor> (V5's echo
#      capability on echo-a, as W2c's row used it in place of a search
#      adapter), and IC-10's refusal on the live binary with tools.enabled.
#      The row "one managed search and one model call in G19's run scope and
#      the request log" is V6's (capability calls take G19's holds only
#      through V6): DEFERRED with it.
#   3. IC-10's TestIC10WiredGatewayRefusesManagedByokOnlyGlob, --- PASS by
#      name. (IC-8, the provider kind, is not on the release line: the MCP
#      port dropped it, release-next/mcp-apps P1.)
#   4. The secret-migration check (duty 3): agentcc_mcp_migrate_secrets
#      --check on the gate database reports zero plaintext MCP secrets.
#
# Environment: SCN_REDIS, SCN_REDIS_DB (required); GATE_HOME; GATE_DJANGO_MANAGE.
set -u
GATES=$(cd "$(dirname "$0")" && pwd)
SC=$(cd "$GATES/.." && pwd)
GW=$(cd "$SC/../.." && pwd)
GATE_HOME=${GATE_HOME:-$HOME/.futureagi-local/gateway-program/lanes/X6-gates}
mkdir -p "$GATE_HOME"
if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "W3b_capabilities: SCN_REDIS and a non-zero SCN_REDIS_DB are required" >&2
  exit 1
fi
status=0

echo "### W3b_capabilities part 1: 08_v6_search.sh DEFERRED (V6 is the next wave's)"

echo "### W3b_capabilities part 2: W3b_capability_ledger_live.sh"
log=$GATE_HOME/w3b-cap-part2-ledger-live.log
if [ -z "${GATE_DJANGO_MANAGE:-}" ]; then
  echo "W3b_capabilities part 2: FAIL (GATE_DJANGO_MANAGE is not set)"
  status=1
elif GATE_HOME=$GATE_HOME bash "$GATES/W3b_capability_ledger_live.sh" >"$log" 2>&1; then
  echo "W3b_capabilities part 2: PASS ($(grep -Eo '[0-9]+ assertions' "$log" | tail -1)); run-scope row DEFERRED with V6"
else
  echo "W3b_capabilities part 2: FAIL (log $log)"
  grep -E "SCN FAIL" "$log" | head -3
  status=1
fi

echo "### W3b_capabilities part 3: IC-10 by name"
p3=$GATE_HOME/w3b-cap-part3.log
(cd "$GW" && go test ./cmd/agentcc \
  -run '^TestIC10WiredGatewayRefusesManagedByokOnlyGlob$' -count=1 -v) >"$p3" 2>&1
rc=$?
missing=0
for name in TestIC10WiredGatewayRefusesManagedByokOnlyGlob; do
  grep -Eq -- "^--- PASS: $name " "$p3" || { echo "  missing PASS for $name"; missing=1; }
done
if [ $rc -eq 0 ] && [ $missing -eq 0 ]; then
  echo "W3b_capabilities part 3: PASS"
else
  echo "W3b_capabilities part 3: FAIL (exit $rc; log $p3)"
  status=1
fi

echo "### W3b_capabilities part 4: agentcc_mcp_migrate_secrets --check on the gate database"
p4=$GATE_HOME/w3b-cap-part4-migrate-secrets.log
if [ -z "${GATE_DJANGO_MANAGE:-}" ]; then
  echo "W3b_capabilities part 4: FAIL (GATE_DJANGO_MANAGE is not set)"
  status=1
elif bash "$GATE_DJANGO_MANAGE" agentcc_mcp_migrate_secrets --check >"$p4" 2>&1 &&
  grep -q "No plaintext MCP secrets in org configs." "$p4"; then
  echo "W3b_capabilities part 4: PASS (zero plaintext MCP secrets)"
else
  echo "W3b_capabilities part 4: FAIL (log $p4)"
  tail -5 "$p4"
  status=1
fi

if [ $status -eq 0 ]; then echo "W3b_capabilities: PASS"; else echo "W3b_capabilities: FAIL"; fi
exit $status
