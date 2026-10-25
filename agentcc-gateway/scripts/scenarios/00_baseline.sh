#!/usr/bin/env bash
# Baseline: the managed-models checks on the real binary, against the mock
# upstream.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/00_baseline.sh
#
# Check 2 is "a zero-granted account gets 402"; an opted-in org with no
# account at all gets 503 managed_billing_unavailable (15_credits.sh, check 3).
set -u
source "$(dirname "$0")/lib.sh"
scn_up

ORG=$SCN_ORG
SONNET='{"model":"anthropic/claude-sonnet-5.5","messages":[{"role":"user","content":"hi"}]}'
# A stream's usage chunk goes only to a client that asks for it (OpenAI's
# stream_options.include_usage contract).
SONNET_USAGE='{"model":"anthropic/claude-sonnet-5.5","stream_options":{"include_usage":true},"messages":[{"role":"user","content":"hi"}]}'
GPT4O='{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}'
GRANTED=5
BILLED=0 # USD billed so far, from x-agentcc-cost and streamed usage.cost

upstream_chats() { scn_received 0 /chat/completions | jq '.data | length'; }
operator_calls() {
  scn_received 0 /chat/completions |
    jq '[.data[] | select(.headers.authorization == "Bearer scn-test-operator-upstream")] | length'
}
balance() { scn_admin GET "/-/orgs/$ORG/credits" >/dev/null && jq -r .balance_usd <"$SCN_DIR/last.body"; }
add_billed() { BILLED=$(python3 -I -c 'import sys; from decimal import Decimal as D; print(D(sys.argv[1]) + D(sys.argv[2]))' "$BILLED" "$1"); }
usd6() { python3 -I -c 'import sys; from decimal import Decimal as D; print("%.6f" % D(sys.argv[1]))' "$1"; }
usd_diff() { python3 -I -c 'import sys; from decimal import Decimal as D; print("%.6f" % (D(sys.argv[1]) - D(sys.argv[2])))' "$1" "$2"; }
refused() { if [ "$1" != 200 ]; then echo refused; else echo "served (HTTP 200)"; fi; }

scn_check "1. an org that has not opted in cannot use a managed model"
scn_org_config "$ORG" '{}'
before=$(upstream_chats)
scn_assert_eq 1.status 403 "$(scn_chat "$SONNET")"
scn_assert_eq 1.upstream_not_called "$before" "$(upstream_chats)"

scn_check "2. an opted-in org whose account was granted \$0 gets 402"
scn_org_config "$ORG" '{"managed_models":{"enabled":true}}'
scn_grant "$ORG" 0
before=$(upstream_chats)
scn_assert_eq 2.status 402 "$(scn_chat "$SONNET")"
scn_assert_jq 2.error_code .error.code managed_credits_exhausted
scn_assert_eq 2.upstream_not_called "$before" "$(upstream_chats)"

scn_check "3. after a \$$GRANTED grant, a managed call is billed upstream cost + 5% (0.0054 -> 0.00567)"
scn_grant "$ORG" "$GRANTED"
charged=$(scn_charges)
scn_assert_eq 3.status 200 "$(scn_chat "$SONNET")"
scn_assert_eq 3.billing_mode managed "$(scn_hdr x-agentcc-billing-mode)"
scn_assert_eq 3.provider openrouter "$(scn_hdr x-agentcc-provider)"
scn_assert_eq 3.cost_header 0.005670 "$(scn_hdr x-agentcc-cost)"
scn_assert_eq 3.usage_cost 0.00567 "$(scn_json 'd["usage"]["cost"]')"
add_billed "$(scn_hdr x-agentcc-cost)"
scn_assert_eq 3.upstream_charge_micros 5400 "$(( $(scn_charges) - charged ))"
scn_received 1 /chat/completions >"$SCN_DIR/received.json"
scn_assert_jq 3.upstream_key '.data[0].headers.authorization' 'Bearer scn-test-managed-upstream' "$SCN_DIR/received.json"
scn_assert_eq 3.balance "$(usd_diff "$GRANTED" "$BILLED")" "$(usd6 "$(balance)")"

scn_check "4. model auto: a one-line question goes to the economical model"
scn_assert_eq 4.status 200 "$(scn_chat '{"model":"auto","messages":[{"role":"user","content":"What is the capital of France?"}]}')"
scn_assert_eq 4.auto_model qwen/qwen3.7-flash "$(scn_hdr x-agentcc-auto-model)"
scn_assert_eq 4.auto_tier simple "$(scn_hdr x-agentcc-auto-tier)"
scn_assert_eq 4.billing_mode managed "$(scn_hdr x-agentcc-billing-mode)"
add_billed "$(scn_hdr x-agentcc-cost)"

scn_check "5. model auto: a long tool-using agent request goes to the capable model"
AGENT=$(python3 -I -c '
import json
tool = lambda n: {"type": "function", "function": {"name": n, "parameters": {"type": "object"}}}
ask = "Look up the order status and the refund policy, then explain step by step. " * 40
print(json.dumps({"model": "auto", "tools": [tool("get_order"), tool("get_policy"), tool("refund")],
                  "messages": [{"role": "system", "content": "You are a careful support agent that uses tools."},
                               {"role": "user", "content": ask}]}))')
scn_assert_eq 5.status 200 "$(scn_chat "$AGENT")"
scn_assert_eq 5.auto_model anthropic/claude-sonnet-5.5 "$(scn_hdr x-agentcc-auto-model)"
scn_assert_eq 5.auto_tier complex "$(scn_hdr x-agentcc-auto-tier)"
add_billed "$(scn_hdr x-agentcc-cost)"

scn_check "6. a customer key cannot lock onto FutureAGI's operator provider"
before=$(operator_calls)
scn_assert_eq 6.status 403 "$(scn_chat "$GPT4O" -H 'x-agentcc-provider-lock: operator-openai')"
scn_assert_eq 6.operator_not_called "$before" "$(operator_calls)"

scn_check "7. a fallback may move to another managed model, never to the operator provider"
scn_org_config "$ORG" '{"managed_models":{"enabled":true},"routing":{"fallback_enabled":true,"model_fallbacks":{"anthropic/claude-sonnet-5.5":["qwen/qwen3.7-flash"]}}}'
scn_behavior fail:503 anthropic/claude-sonnet-5.5 >/dev/null
scn_assert_eq 7.managed_fallback_status 200 "$(scn_chat "$SONNET")"
scn_assert_eq 7.managed_fallback_model qwen/qwen3.7-flash "$(scn_hdr x-agentcc-model-used)"
add_billed "$(scn_hdr x-agentcc-cost)"
scn_behavior_clear >/dev/null
scn_org_config "$ORG" '{"managed_models":{"enabled":true},"routing":{"fallback_enabled":true,"model_fallbacks":{"anthropic/claude-sonnet-5.5":["gpt-4o"]}}}'
before=$(operator_calls)
scn_assert_eq 7.operator_fallback_refused refused "$(refused "$(scn_chat "$SONNET" -H 'x-mock-behavior: fail:503')")"
scn_assert_eq 7.operator_not_called "$before" "$(operator_calls)"
scn_org_config "$ORG" '{"managed_models":{"enabled":true}}'

scn_check "8. a managed stream is billed, and shows the billed cost, not the upstream's"
balance_before=$(balance)
charged=$(scn_charges)
scn_assert_eq 8.status 200 "$(scn_stream "$SONNET_USAGE")"
scn_assert_eq 8.ends_with_done '[DONE]' "$(scn_json 'd[-1]')"
scn_assert_eq 8.one_usage_chunk_before_done 1,True "$(scn_json '"%d,%s" % (len([c for c in d if isinstance(c, dict) and not c.get("choices")]), isinstance(d[-2], dict) and d[-2].get("choices") == [])')"
scn_assert_eq 8.final_usage_cost 0.00567 "$(scn_json '[c for c in d if isinstance(c, dict) and c.get("usage")][-1]["usage"]["cost"]')"
add_billed 0.00567
scn_assert_eq 8.upstream_charge_micros 5400 "$(( $(scn_charges) - charged ))"
scn_assert_eq 8.balance_drawn 0.005670 "$(usd_diff "$balance_before" "$(balance)")"

scn_check "9. the credits admin endpoint reports the balance, and only to the admin token"
scn_assert_eq 9.status 200 "$(scn_admin GET "/-/orgs/$ORG/credits")"
scn_assert_jq 9.markup .markup_percent 5
scn_assert_eq 9.balance "$(usd_diff "$GRANTED" "$BILLED")" "$(usd6 "$(jq -r .balance_usd <"$SCN_DIR/last.body")")"
scn_assert_eq 9.no_token 401 "$(curl -sS -o /dev/null -w '%{http_code}' "$SCN_GW/-/orgs/$ORG/credits")"
scn_assert_eq 9.wrong_token 401 "$(curl -sS -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer scn-wrong' "$SCN_GW/-/orgs/$ORG/credits")"

scn_pass
