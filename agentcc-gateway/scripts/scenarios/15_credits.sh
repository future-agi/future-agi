#!/usr/bin/env bash
# The managed-credit hot path through the real gateway binary, Redis and
# the mock upstream.
#  1. Granted $0.01 at version 1, five parallel managed calls held at about
#     $0.004 each: every refusal is a 402 naming its limit_source, the org's
#     outbox holds exactly one record per 200, spent_micros is the sum of
#     what the 200s were billed, and nothing stays held.
#  2. Re-pushing version 1 is not applied.
#  3. After the org's account hash is lost (DEL mc:{<org>}:acct), the next
#     managed call is 503 managed_billing_unavailable (account_state_missing,
#     Retry-After 30), never a 402, and the org is in mc:needs_push.
#  4. A version 2 push with the settled-spend floor restores spent to the
#     floor, and managed calls succeed again.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/15_credits.sh
set -u
source "$(dirname "$0")/lib.sh"
[ -n "${SCN_REDIS:-}" ] || scn_fail 15.redis "SCN_REDIS and SCN_REDIS_DB are required: managed billing fails closed without Redis"
scn_up

ORG=$SCN_ORG
# "hi" is 8 input tokens: (8 × $2 + 380 × $10) per million × 1.05 = 4,007
# micros held per call.
CALL='{"model":"anthropic/claude-sonnet-5.5","max_tokens":380,"messages":[{"role":"user","content":"hi"}]}'

# rcmd <command> [args...]: one Redis command in the run's DB; prints the
# reply (an integer, or a bulk string).
rcmd() {
  python3 -I - "$SCN_REDIS" "${SCN_REDIS_DB:-0}" "$@" <<'PY'
import socket, sys
host, port = sys.argv[1].rsplit(":", 1)
sock = socket.create_connection((host, int(port)), timeout=5)
rd = sock.makefile("rb")
def reply():
    line = rd.readline()
    kind, rest = line[:1], line[1:-2]
    if kind == b"-":
        raise SystemExit("redis: " + rest.decode())
    if kind in (b"+", b":"):
        return rest.decode()
    if kind == b"$":
        n = int(rest)
        return "" if n < 0 else rd.read(n + 2)[:-2].decode()
    return [reply() for _ in range(int(rest))]
def cmd(*args):
    out = b"*%d\r\n" % len(args)
    for a in args:
        a = str(a).encode()
        out += b"$%d\r\n%s\r\n" % (len(a), a)
    sock.sendall(out)
    return reply()
cmd("SELECT", sys.argv[2])
print(cmd(*sys.argv[3:]))
PY
}

credits_field() { # <field>: a field of GET /-/orgs/$ORG/credits
  scn_admin GET "/-/orgs/$ORG/credits" >/dev/null
  jq -r ".$1" <"$SCN_DIR/last.body"
}
usd_to_micros() { python3 -I -c 'import sys; from decimal import Decimal; print(int(Decimal(sys.argv[1]) * 1000000))' "$1"; }
header_of() { tr -d '\r' <"$1" | awk -v h="$2" 'tolower($1) == tolower(h)":" {print $2}' | tail -1; }

scn_org_config "$ORG" '{"managed_models":{"enabled":true}}'
scn_grant "$ORG" 0.01

scn_check "1. \$0.01 granted, five parallel managed calls held at about \$0.004 each"
scn_assert_eq 1.outbox_empty 0 "$(rcmd XLEN "mc:{$ORG}:outbox")"
pids=()
for i in 1 2 3 4 5; do
  curl -sS -o "$SCN_DIR/par.$i.body" -D "$SCN_DIR/par.$i.headers" -w '%{http_code}' \
    -X POST "$SCN_GW/v1/chat/completions" -H "Authorization: Bearer $SCN_KEY" \
    -H 'Content-Type: application/json' --data-binary "$CALL" >"$SCN_DIR/par.$i.code" &
  pids+=($!)
done
wait "${pids[@]}" # only the calls: the gateway and the mock are this shell's jobs too
ok=0 refused=0 billed=0
for i in 1 2 3 4 5; do
  code=$(cat "$SCN_DIR/par.$i.code")
  case "$code" in
    200)
      ok=$((ok + 1))
      cost=$(header_of "$SCN_DIR/par.$i.headers" x-agentcc-cost)
      [ -n "$cost" ] || scn_fail "1.call_$i.cost" "a 200 carries no x-agentcc-cost: $(cat "$SCN_DIR/par.$i.headers")"
      billed=$((billed + $(usd_to_micros "$cost"))) ;;
    402)
      refused=$((refused + 1))
      scn_assert_jq "1.call_$i.limit_source" '.error.metadata.limit_source | IN("agentcc_credits", "agentcc_in_flight")' true "$SCN_DIR/par.$i.body"
      scn_assert_jq "1.call_$i.code" '.error.code | startswith("managed_credits_")' true "$SCN_DIR/par.$i.body"
      scn_assert_jq "1.call_$i.scope" '.error.metadata.scope' org "$SCN_DIR/par.$i.body"
      scn_assert_jq "1.call_$i.resets_at_null" '.error.metadata | has("resets_at") and .resets_at == null' true "$SCN_DIR/par.$i.body" ;;
    *) scn_fail "1.call_$i.status" "HTTP $code, want 200 or 402: $(cat "$SCN_DIR/par.$i.body")" ;;
  esac
done
echo "  $ok served, $refused refused, $billed micros billed"
[ "$ok" -ge 1 ] || scn_fail 1.some_served "no call was served from \$0.01"
[ "$refused" -ge 1 ] || scn_fail 1.some_refused "all five \$0.004 holds fit in \$0.01"
scn_assert_eq 1.outbox_one_per_200 "$ok" "$(rcmd XLEN "mc:{$ORG}:outbox")"
scn_assert_eq 1.spent_is_sum_billed "$billed" "$(credits_field spent_micros)"
scn_assert_eq 1.nothing_held 0 "$(credits_field held_micros)"
scn_assert_eq 1.ledger redis "$(credits_field ledger)"
scn_assert_eq 1.outbox_org_hint 1 "$(rcmd SISMEMBER mc:outbox_orgs "$ORG")"

scn_check "2. re-pushing version 1 is not applied"
scn_assert_eq 2.status 200 "$(scn_admin PUT "/-/orgs/$ORG/credits/granted" '{"granted_total_micros": 10000, "version": 1, "settled_spend_floor_micros": 0}')"
scn_assert_jq 2.applied .applied false
scn_assert_jq 2.version_kept .granted_version 1
scn_assert_jq 2.spent_kept .spent_micros "$billed"

scn_check "3. a lost account hash is a 503 that asks for a re-push, never a 402"
scn_assert_eq 3.deleted 1 "$(rcmd DEL "mc:{$ORG}:acct")"
before=$(scn_received 0 /chat/completions | jq '.data | length')
scn_assert_eq 3.status 503 "$(scn_chat "$CALL")"
scn_assert_jq 3.code .error.code managed_billing_unavailable
scn_assert_jq 3.reason .error.metadata.reason account_state_missing
scn_assert_eq 3.retry_after 30 "$(scn_hdr retry-after)"
scn_assert_eq 3.needs_push 1 "$(rcmd SISMEMBER mc:needs_push "$ORG")"
scn_assert_eq 3.upstream_not_called "$before" "$(scn_received 0 /chat/completions | jq '.data | length')"

scn_check "4. a version 2 push with the settled-spend floor restores spent and service"
push=$(jq -cn --argjson total $((billed + 1000000)) --argjson floor "$billed" \
  '{granted_total_micros: $total, version: 2, settled_spend_floor_micros: $floor}')
scn_assert_eq 4.status 200 "$(scn_admin PUT "/-/orgs/$ORG/credits/granted" "$push")"
scn_assert_jq 4.applied .applied true
scn_assert_jq 4.spent_restored .spent_micros "$billed"
scn_assert_jq 4.available .available_micros 1000000
scn_assert_eq 4.spent_read_back "$billed" "$(credits_field spent_micros)"
scn_assert_eq 4.call_served 200 "$(scn_chat "$CALL")"
after=$(usd_to_micros "$(scn_hdr x-agentcc-cost)")
scn_assert_eq 4.spent_grows "$((billed + after))" "$(credits_field spent_micros)"
scn_assert_eq 4.outbox_grows "$((ok + 1))" "$(rcmd XLEN "mc:{$ORG}:outbox")"
scn_assert_eq 4.nothing_held 0 "$(credits_field held_micros)"

scn_pass
