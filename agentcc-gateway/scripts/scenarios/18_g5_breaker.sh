#!/usr/bin/env bash
# Circuits are per model, through the real gateway binary and the mock
# upstream (fixtures/G5/gateway.yaml). Failover is on and the global breaker
# opens a circuit after 5 failures.
#  1. 25 requests to the managed mock/fail-500 open that model's circuits:
#     the upstream sees exactly 5 failed calls on each of openrouter and
#     backup, then nothing more.
#  2. A request to managed mock/ok-1 still returns 200 through openrouter, the
#     same provider: one failing model does not open the provider.
#  3. The failing model's next request is refused by its open circuits with a
#     503 all_providers_unavailable, without reaching the upstream.
#  4. The provider view (/-/health/providers) aggregates openrouter's circuits:
#     one model open, one closed reads half-open, and healthy.
#
# Checks 2 to 4 need the failover record site to feed the per-model circuits
# the failover checks: the merged tree converts it, so this script runs as the
# gate check gates/W1_breaker_per_model.sh on the integrated tree. In this
# change alone the record site still feeds the provider-level circuit, which
# marks the whole provider unhealthy, and check 2 fails.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<lane db> bash scripts/scenarios/18_g5_breaker.sh
set -u
G5_FIXTURES="$(cd "$(dirname "$0")" && pwd)/fixtures/G5"
SCN_CONFIG_TEMPLATE="$G5_FIXTURES/gateway.yaml"
INTERNAL_KEY=scn-test-key-g5-internal
source "$(dirname "$0")/lib.sh"
scn_up

FAILING='{"model":"mock/fail-500","messages":[{"role":"user","content":"hi"}]}'
OK='{"model":"mock/ok-1","messages":[{"role":"user","content":"hi"}]}'

# upstream_calls <model> [authorization]: chat calls the mock received for model.
upstream_calls() {
  scn_received 0 /chat/completions |
    jq --arg m "$1" --arg auth "${2:-}" \
      '[.data[] | select(.body.model == $m) | select($auth == "" or .headers.authorization == $auth)] | length'
}
internal_chat() { SCN_KEY=$INTERNAL_KEY scn_chat "$@"; }

scn_check "1. 25 requests to mock/fail-500 open its circuits after 5 failures each"
served=0
for _ in $(seq 25); do
  [ "$(internal_chat "$FAILING")" = 200 ] && served=$((served + 1))
done
scn_assert_eq 1.none_served 0 "$served"
scn_assert_eq 1.openrouter_calls 5 "$(upstream_calls mock/fail-500 'Bearer scn-test-managed-upstream')"
scn_assert_eq 1.backup_calls 5 "$(upstream_calls mock/fail-500 'Bearer scn-test-g5-backup-upstream')"

scn_check "2. mock/ok-1 still returns 200 through the same provider"
scn_assert_eq 2.status 200 "$(internal_chat "$OK")"
scn_assert_eq 2.provider openrouter "$(scn_hdr x-agentcc-provider)"

scn_check "3. the failing model's next request is refused by its open circuit"
before=$(upstream_calls mock/fail-500)
scn_assert_eq 3.status 503 "$(internal_chat "$FAILING")"
scn_assert_jq 3.error_code .error.code all_providers_unavailable
scn_assert_eq 3.upstream_not_called "$before" "$(upstream_calls mock/fail-500)"

scn_check "4. the provider view aggregates openrouter's per-model circuits"
scn_admin GET /-/health/providers >/dev/null
scn_assert_jq 4.circuit_state '.providers[] | select(.id == "openrouter") | .circuit_state' half-open
scn_assert_jq 4.healthy '.providers[] | select(.id == "openrouter") | .healthy' true

scn_pass
