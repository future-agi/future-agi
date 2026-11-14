// MOCK_READING — the read-audit projection of the v1 customer-support world
// (MOCK_WORLD, world.js). It truncates the rule strings for display and drops
// tool descriptions/args; world.js reads the tool names off it at full fidelity
// so a rename here fails the scenario tests loudly rather than drifting.
//
// This is the sole surviving fragment of the old designer read-audit fixtures
// (preflightFails.js, since deleted with the mock read-audit UI). It is kept
// only because MOCK_WORLD derives its tool set from it; the failing-check
// questions/section-issues that lived alongside it are gone — the source panels
// now render the real preflight `checks[]` inline.
import { ORIGIN_ID } from "src/sections/simulate/environments/buildEnvironment/provenance.constants";

// 12 tools — the first four read from config, the rest from the call-graph.
export const MOCK_READING = {
  tools: [
    { name: "verify_identity", origin: ORIGIN_ID.CONFIG },
    { name: "send_otp", origin: ORIGIN_ID.CONFIG },
    { name: "check_otp", origin: ORIGIN_ID.CONFIG },
    { name: "create_guest_customer", origin: ORIGIN_ID.CONFIG },
    { name: "lookup_order", origin: ORIGIN_ID.CALL_GRAPH },
    { name: "get_return_window", origin: ORIGIN_ID.CALL_GRAPH },
    { name: "get_refund_quote", origin: ORIGIN_ID.CALL_GRAPH },
    { name: "issue_refund", origin: ORIGIN_ID.CALL_GRAPH },
    { name: "send_replacement", origin: ORIGIN_ID.CALL_GRAPH },
    { name: "get_refund_status", origin: ORIGIN_ID.CALL_GRAPH },
    { name: "apply_goodwill_credit", origin: ORIGIN_ID.CALL_GRAPH },
    { name: "escalate_to_human", origin: ORIGIN_ID.CALL_GRAPH },
  ],
  // 5 rules read from policy.yaml — names truncated at 42 chars + "…".
  rules: [
    { name: "Never issue a refund outside the return wi…", origin: ORIGIN_ID.POLICY },
    { name: "Never read back more than the last four di…", origin: ORIGIN_ID.POLICY },
    { name: "Verify identity before disclosing or chang…", origin: ORIGIN_ID.POLICY },
    { name: "An OTP must be read aloud by the caller — …", origin: ORIGIN_ID.POLICY },
    { name: "Goodwill credit is capped at £25 per call.", origin: ORIGIN_ID.POLICY },
  ],
  // 4 seed fixtures (SEED sliced to the first four).
  data: [
    { name: "customers.csv", note: "240 rows", origin: ORIGIN_ID.FIXTURE },
    { name: "orders.csv", note: "610 rows", origin: ORIGIN_ID.FIXTURE },
    { name: "returns.csv", note: "95 rows", origin: ORIGIN_ID.FIXTURE },
    { name: "payments.csv", note: "480 rows", origin: ORIGIN_ID.FIXTURE },
  ],
  // 3 hand-picked behavior facts illustrating the prompt/inferred distinction.
  behavior: [
    { name: "routing prompt", note: "11 branches", origin: ORIGIN_ID.PROMPT },
    { name: "transfer → front desk", origin: ORIGIN_ID.PROMPT },
    { name: "escalate on 2× refusal", origin: ORIGIN_ID.INFERRED },
  ],
};
