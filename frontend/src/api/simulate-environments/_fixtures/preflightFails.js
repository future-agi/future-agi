// Designer failing-check fixtures (prototype f9201bb4d). The real backend
// preflight returns happy-path only today, so preflightReadAudit overlays these
// so the read-audit has content. MOCK_READING is the designer's buildReading
// output for the derivedEnvironment fixture (BuildFromAgent.jsx:609–632 over
// _mock/builder.js TOOLS/RULES/SEED); MOCK_QUESTIONS is buildQuestions capped at
// two. TODO: delete this file when the response carries reading, questions and
// section issues of its own.
import { ORIGIN_ID } from "src/sections/simulate/environments/buildEnvironment/provenance.constants";
import {
  MOCK_SECTION_ISSUES as SECTION_ISSUES,
  READ_AUDIT_COPY,
} from "src/sections/simulate/environments/buildEnvironment/readAudit.constants";

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

// buildQuestions for the derivedEnvironment, capped at 2 (the receipt is an
// audit, not an intake form). The last tool drives the first question.
export const MOCK_QUESTIONS = [
  {
    id: "tool-side-effects",
    title: "Does escalate_to_human change data?",
    why: "It's called from your code but never described in the prompt. Your answer decides whether a scenario may trigger real side-effects.",
    kind: "choice",
    options: [
      { id: "read", label: "Read-only" },
      { id: "write", label: "Writes to state" },
      { id: "escalate", label: "Escalates externally" },
    ],
  },
  {
    id: "policy-enforcement",
    title:
      "Are the policy values enforced in your backend, or only stated in the prompt?",
    why: "We can see the values, not where they're enforced. A rule we only infer is graded more softly than a hard one.",
    kind: "boolean",
  },
];

// The mock section gaps live in the constants module so ReadAudit's URL-forced
// demo states and this overlay share one source. Re-exported for the overlay.
export const MOCK_SECTION_ISSUES = SECTION_ISSUES;

// Reason shown on the demo hard-fail page (the designer's default copy).
export const MOCK_HARDFAIL_REASON = READ_AUDIT_COPY.hardfailDefault;
