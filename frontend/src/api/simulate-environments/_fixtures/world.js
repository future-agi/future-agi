// MOCK_WORLD — the fixture world the client-side build flow seeds from (see
// workspace/helpers/seedEnvState.js) when the read-audit it gets back is only a
// display projection. It is NOT an overlay for real environments: a harness-backed
// environment shows only what its own stage outputs carry, so this fixture never
// fills a gap on one. It is the full-fidelity version of the same
// customer-support world Phase-2 already surfaces in the read-audit
// (preflightFails.js MOCK_READING), plus the designer's `env-voice-support`
// metadata (id/name/surface/domain/tagline/description/evalPreset).
//
// MOCK_READING is the read-audit projection of this world: it truncates the
// rule strings for display and drops tool descriptions/args. The scenario
// derivation needs the untruncated rules and the tool descriptions, so those
// are supplied here at full fidelity. The tool set (names, count and order) is
// read straight off MOCK_READING, so a rename there fails the scenario tests
// loudly rather than silently drifting.
import { MOCK_READING } from "./preflightFails";

// Descriptions + argument lists for each MOCK_READING tool, keyed by name.
const TOOL_DETAILS = {
  verify_identity: { args: ["phone", "postcode"], desc: "Match a caller to an account before touching it." },
  send_otp: { args: [], desc: "Text a one-time code to the number on file." },
  check_otp: { args: ["code"], desc: "Verify a code the caller reads back. Never inferred." },
  create_guest_customer: { args: ["first_name"], desc: "Open a guest record when a caller has no account." },
  lookup_order: { args: ["order_id"], desc: "Fetch an order, its status and delivery history." },
  get_return_window: { args: ["order_id"], desc: "Days remaining, and whether the item is excluded." },
  get_refund_quote: { args: ["order_id", "reason"], desc: "What would be refunded, before anything is promised." },
  issue_refund: { args: ["order_id", "amount", "caller_confirmed"], desc: "Refund to the original payment method." },
  send_replacement: { args: ["order_id", "reason"], desc: "Ship a replacement instead of refunding." },
  get_refund_status: { args: ["order_id"], desc: "Where an in-flight refund has got to." },
  apply_goodwill_credit: { args: ["amount"], desc: "Store credit, capped by policy." },
  escalate_to_human: { args: ["reason"], desc: "Hand the call to a person." },
};

// No fallback on purpose: a tool renamed in MOCK_READING has no entry here and
// throws at module load, rather than silently shipping a placeholder desc.
const tools = MOCK_READING.tools.map((t) => ({
  name: t.name,
  args: TOOL_DETAILS[t.name].args,
  desc: TOOL_DETAILS[t.name].desc,
}));

// The untruncated policy strings the read-audit shortens for display.
const rules = [
  "Never issue a refund outside the return window without a supervisor.",
  "Never read back more than the last four digits of a card.",
  "Verify identity before disclosing or changing anything on an account.",
  "An OTP must be read aloud by the caller — never inferred or guessed.",
  "Goodwill credit is capped at £25 per call.",
];

// Seed tables — MOCK_READING.data is "SEED sliced to the first four", so this is
// that same four at full fidelity: the row counts plus the complication notes
// each trap scenario is derived from. Keeping it to four means the workspace
// shows exactly the tables the read-audit named.
const seed = {
  tables: [
    { name: "customers", rows: 240, note: "40 with saved cards, 12 guests" },
    { name: "orders", rows: 610, note: "18% delivered outside the window" },
    { name: "returns", rows: 95, note: "22 excluded items" },
    { name: "payments", rows: 480, note: "9 expired cards" },
  ],
};

export const MOCK_WORLD = {
  id: "env-voice-support",
  agentType: "voice_platform",
  name: "Customer Support Line",
  surface: "voice",
  domain: "ecommerce",
  tagline: "Inbound phone support for an online storefront",
  description:
    "A returns-and-orders phone line for a mid-size retailer. The agent answers calls chasing deliveries, requesting refunds and disputing charges.",
  difficulty: "Starter",
  tools,
  rules,
  seed,
  evalPreset: ["task_success", "policy_adherence", "tone", "latency"],
};
