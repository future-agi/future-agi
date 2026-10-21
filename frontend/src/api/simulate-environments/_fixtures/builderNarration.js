// Builder-narration fixture — the three derivation stages, the ask replies and
// the aside-chip filter, ported verbatim from the designer's simulate-v2
// _mock/builder.js (STAGES 323–382, ASKS 384–415, HARNESS_STAGES chips 277–281)
// so the building-stage engine (useBuildProgress) has content until the real
// job poller lands. TOOLS/RULES/SEED exist only as string inputs to the steps.

const TOOLS = [
  { name: "verify_identity", args: ["phone", "postcode"], desc: "Match a caller to an account before touching it." },
  { name: "send_otp", args: [], desc: "Text a one-time code to the number on file." },
  { name: "check_otp", args: ["code"], desc: "Verify a code the caller reads back. Never inferred." },
  { name: "create_guest_customer", args: ["first_name"], desc: "Open a guest record when a caller has no account." },
  { name: "lookup_order", args: ["order_id"], desc: "Fetch an order, its status and delivery history." },
  { name: "get_return_window", args: ["order_id"], desc: "Days remaining, and whether the item is excluded." },
  { name: "get_refund_quote", args: ["order_id", "reason"], desc: "What would be refunded, before anything is promised." },
  { name: "issue_refund", args: ["order_id", "amount", "caller_confirmed"], desc: "Refund to the original payment method." },
  { name: "send_replacement", args: ["order_id", "reason"], desc: "Ship a replacement instead of refunding." },
  { name: "get_refund_status", args: ["order_id"], desc: "Where an in-flight refund has got to." },
  { name: "apply_goodwill_credit", args: ["amount"], desc: "Store credit, capped by policy." },
  { name: "escalate_to_human", args: ["reason"], desc: "Hand the call to a person." },
];

const RULES = [
  "Never issue a refund outside the return window without a supervisor.",
  "Never read back more than the last four digits of a card.",
  "Verify identity before disclosing or changing anything on an account.",
  "An OTP must be read aloud by the caller — never inferred or guessed.",
  "Goodwill credit is capped at £25 per call.",
];

const SEED = [
  { name: "customers", rows: 240, note: "40 with saved cards, 12 guests" },
  { name: "orders", rows: 610, note: "18% delivered outside the window" },
  { name: "returns", rows: 95, note: "22 excluded items" },
  { name: "payments", rows: 480, note: "9 expired cards" },
  { name: "policies", rows: 12, note: "window, exclusions, credit cap" },
];

const think = (text) => ({ kind: "think", text });
const tool = (label, result) => ({ kind: "tool", label, result });
const file = (path, note) => ({ kind: "file", path, note });
const note = (text) => ({ kind: "note", text });
const json = (label, value) => ({ kind: "json", label, value });

// The three narrated stages. `understand` interpolates the agent reference, so
// it is a function of agentRef; `build`/`scenarios` are static objects.
export const NARRATION_STAGES = {
  understand: (agentRef) => ({
    title: "Understanding the agent",
    steps: [
      think(`Reading ${agentRef} — entrypoint, tool registry, prompt package.`),
      tool("read_source", "142 files · Python 3.12 · Dockerfile, db/schema.sql"),
      think("Taking each tool's signature from the code rather than its name, so the arguments and their permitted values are exact."),
      tool("extract_tools", `${TOOLS.length} tools · 9 with required arguments`),
      json("issue_refund", "order_id: str · amount: float · caller_confirmed: bool (must be true)"),
      think("Separating rules the code enforces from rules only the prompt states — the second kind is where agents drift."),
      tool("extract_rules", `${RULES.length} hard rules · 2 enforced in code, 2 prompt-only, 1 found in prose`),
      file("contract.json", "tools, permitted values, hard rules, the real data"),
      note(
        "Contract written from the source, not from a form — nothing here was typed by hand. " +
        "Three of the five rules are not enforced anywhere in code, so the code will happily let the agent break them — those are worth the hardest scenarios. " +
        "One of the three was found in a README rather than the prompt, and a README is writable by anything in the repo, so it is recorded with its origin and held back until you accept it.",
      ),
    ],
  }),

  build: {
    title: "Building the world its tools act on",
    steps: [
      think("The agent's tools have to hit something that answers truthfully, including a truthful refusal."),
      tool("write_handlers", `${TOOLS.length} handlers · one per tool`),
      think("Seeding what the use cases need, including the awkward rows — a world of happy customers proves nothing."),
      tool("seed_world", "1,437 rows across 5 tables"),
      json("seeded_edges", "18% of orders outside the window · 22 excluded items · 9 expired cards · 12 guests"),
      tool("write_checks", "12 sub-goals · each check written as code in checks/<goal>.py"),
      file("world/handlers.py", "answers every tool call from real state"),
      note(
        "The world is up and resettable. Grading is settled from world state and every tool call, " +
        "so a scenario asserts the order is actually cancelled rather than that the agent said so.",
      ),
    ],
  },

  scenarios: {
    title: "Proving scenarios",
    steps: [
      think("One scenario per real use case, each with its own persona brief and sub-goals."),
      tool("draft_scenarios", "8 drafted across the use-case range"),
      think("Three gates, all code, no model: ready, solvable, not vacuous."),
      tool("gate · ready", "8 / 8 the world holds what the scenario presumes"),
      tool("gate · solvable", "8 / 8 the reference solution passes the scenario's own checks"),
      tool("gate · not vacuous", "7 / 8 running nothing must fail the checks"),
      note(
        "One scenario failed the third gate and was rewritten, not kept: its identity check asserted " +
        "\"no modification happened before authentication\", which is trivially true when nothing happened. " +
        "A check that passes while the agent did nothing grades nothing while reporting a result.",
      ),
      tool("gate · not vacuous", "8 / 8 after the rewrite"),
      file("scenarios/", "one folder each: scenario.json, setup.py, ready.py, checks/"),
      note("8 of 8 kept. Only proved scenarios are ever run."),
    ],
  },
};

// Chat asks — first regex match wins, so the order is load-bearing.
export const ASK_REPLIES = [
  {
    match: /tool|argument|permitted/i,
    steps: [
      json("tools", TOOLS.map((t) => `${t.name}(${t.args.join(", ") || ""})`).join(" · ")),
      note("Read from the source, so the argument names and permitted values are the agent's real ones — that is what a hand-typed list could never give you."),
    ],
  },
  {
    match: /seed|data|world/i,
    steps: [
      json("seeded", SEED.map((s) => `${s.name} ${s.rows}`).join(" · ")),
      note("The distribution is the test: 18% of orders sit outside the return window, 22 items are excluded, 9 cards have expired."),
    ],
  },
  {
    match: /rule|guardrail|policy/i,
    steps: [
      json("hard_rules", RULES.join(" · ")),
      note("Two are enforced in code. Two more exist only in the prompt, which is why they are graded rather than guaranteed — and one was read out of prose, so nothing grades against it until you confirm it."),
    ],
  },
  {
    match: /more (scenario|edge|case)/i,
    steps: [
      think("Reading the 8 that exist first so I do not repeat them."),
      tool("draft_scenarios", "4 drafted · abusive caller, double refund attempt, wrong order id, silence mid-call"),
      tool("gates", "4 / 4 passed ready, solvable and not vacuous"),
      note("Four added. The double-refund one is the interesting failure — it needs the agent to notice a refund is already in flight."),
    ],
  },
];

export const ASK_FALLBACK = note(
  "In this prototype I answer on tools, seeded data, rules and adding scenarios — and the stage buttons drive the rest.",
);
