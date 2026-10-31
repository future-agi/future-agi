// Vel's sample World (prototype f0b6d8d8c), shown only with ?uiSample=world.
// None of it describes a real environment: tiers, faults, handler code,
// scenario checks and the Gateway servers are not in our API yet.

export const SAMPLE_AGENT_VERSION = "v4";

export const TIERS = [
  { id: "stub", word: "Fixed", note: "A fixed response. Cheapest and fully deterministic." },
  { id: "replay", word: "Recorded", note: "A real response recorded once and played back. Deterministic, but can go stale." },
  { id: "stateful", word: "Simulated", note: "Answered by the world: reads and writes its seeded data, consistent across every call in a run." },
  { id: "live", word: "Live", note: "The real tool. Not deterministic. Only for read-only tools, and only once you allow it for that tool." },
];

export const FAULT_PRESETS = [
  { id: "none", label: "Clean", line: "No injected faults" },
  { id: "realistic", label: "Realistic", line: "5% timeouts · 3% 5xx errors · +400ms latency" },
  { id: "adversarial", label: "Adversarial", line: "20% timeouts · 10% 5xx errors · 10% rate-limited · +1500ms latency · 5% malformed" },
];

const handler = (name, sig, desc, body) =>
  `from env.runtime import db, audit, require_permission\nfrom env.errors import ToolError\n\n\ndef ${name}(${sig}) -> dict:\n    """${desc}."""\n${body}`;

const lookup = (table, filter) =>
  `    row = db.query(\n        "SELECT * FROM ${table} WHERE ${filter} LIMIT 1",\n        args,\n    ).one_or_none()\n    if row is None:\n        raise ToolError("not_found")\n    return row.as_dict()`;

const mutate = (name, idArg) =>
  `    require_permission("${name}")\n    with db.tx() as tx:\n        target = tx.load(${idArg})\n        target.apply("${name}", args)\n        audit.record("${name}", args, actor="agent")\n    return {"ok": True}`;

const ORDER_ID = { name: "order_id", type: "str", required: true, allowed: "A-#####", mono: true };

// The eight tools of Vel's Customer Support Line: three Recorded, five Simulated.
export const SAMPLE_WORLD_TOOLS = [
  {
    name: "lookup_order",
    desc: "Fetch order by ID, email or phone",
    tier: "replay",
    args: [ORDER_ID, { name: "email", type: "str", required: false, allowed: "email address" }],
    code: handler("lookup_order", "order_id: str, email: str | None = None", "Fetch order by ID, email or phone", `    args = {"order_id": order_id, "email": email}\n${lookup("orders", "id = :order_id")}`),
  },
  {
    name: "shipment_status",
    desc: "Live carrier tracking",
    tier: "replay",
    args: [ORDER_ID],
    code: handler("shipment_status", "order_id: str", "Live carrier tracking", `    args = {"order_id": order_id}\n${lookup("shipments", "order_id = :order_id")}`),
  },
  {
    name: "issue_refund",
    desc: "Refund up to the order total",
    tier: "stateful",
    args: [ORDER_ID, { name: "amount_cents", type: "int", required: true, allowed: "up to the order total" }, { name: "reason", type: "str", required: true, allowed: "any text" }],
    code: handler("issue_refund", "order_id: str, amount_cents: int, reason: str", "Refund up to the order total", `    args = {"order_id": order_id, "amount_cents": amount_cents, "reason": reason}\n${mutate("issue_refund", "order_id")}`),
  },
  {
    name: "escalate_to_human",
    desc: "Warm transfer to a supervisor",
    tier: "stateful",
    args: [{ name: "reason", type: "str", required: true, allowed: "any text" }, { name: "priority", type: "enum", required: false, values: ["normal", "urgent"] }],
    code: handler("escalate_to_human", "reason: str, priority: str = 'normal'", "Warm transfer to a supervisor", `    args = {"reason": reason, "priority": priority}\n    require_permission("escalate_to_human")\n    audit.record("escalate_to_human", args, actor="agent")\n    return {"ok": True}`),
  },
  {
    name: "check_return_eligibility",
    desc: "Confirm a return fits the policy",
    tier: "replay",
    args: [ORDER_ID],
    code: handler("check_return_eligibility", "order_id: str", "Confirm a return fits the policy", `    args = {"order_id": order_id}\n${lookup("returns", "order_id = :order_id")}`),
  },
  {
    name: "generate_return_label",
    desc: "Email a printable return label",
    tier: "stateful",
    args: [ORDER_ID, { name: "email", type: "str", required: true, allowed: "email address" }],
    code: handler("generate_return_label", "order_id: str, email: str", "Email a printable return label", `    args = {"order_id": order_id, "email": email}\n${mutate("generate_return_label", "order_id")}`),
  },
  {
    name: "cancel_order",
    desc: "Cancel while still in warehouse",
    tier: "stateful",
    args: [ORDER_ID],
    code: handler("cancel_order", "order_id: str", "Cancel while still in warehouse", `    args = {"order_id": order_id}\n${mutate("cancel_order", "order_id")}`),
  },
  {
    name: "update_shipping_address",
    desc: "Change delivery before dispatch",
    tier: "stateful",
    args: [ORDER_ID, { name: "new_address", type: "object", required: true, allowed: "street, city, postcode" }],
    code: handler("update_shipping_address", "order_id: str, new_address: dict", "Change delivery before dispatch", `    args = {"order_id": order_id, "new_address": new_address}\n    with db.tx() as tx:\n        target = tx.load(order_id)\n        target.update(address=new_address)\n        audit.record("update_shipping_address", args, actor="agent")\n    return {"ok": True}`),
  },
];

// Vel's four seeded tables with their columns; pk marks the primary key.
export const SAMPLE_SCHEMA = [
  {
    name: "orders", rows: 500, note: "12% delayed, 6% lost in transit",
    cols: [
      { name: "id", type: "uuid", pk: true },
      { name: "customer_email", type: "text" },
      { name: "status", type: "enum(open,shipped,cancelled,refunded)" },
      { name: "total_cents", type: "integer" },
      { name: "placed_at", type: "timestamptz" },
      { name: "shipped_at", type: "timestamptz" },
    ],
  },
  {
    name: "customers", rows: 200, note: "40 with loyalty tier",
    cols: [
      { name: "id", type: "uuid", pk: true },
      { name: "email", type: "text" },
      { name: "name", type: "text" },
      { name: "loyalty_tier", type: "enum(bronze,silver,gold,platinum)" },
      { name: "created_at", type: "timestamptz" },
    ],
  },
  {
    name: "returns", rows: 85, note: "20 outside the return window",
    cols: [
      { name: "id", type: "uuid", pk: true },
      { name: "order_id", type: "uuid → orders.id" },
      { name: "reason", type: "text" },
      { name: "opened_at", type: "timestamptz" },
      { name: "outside_window", type: "boolean" },
    ],
  },
  {
    name: "products", rows: 340, note: "18 discontinued",
    cols: [
      { name: "sku", type: "text", pk: true },
      { name: "name", type: "text" },
      { name: "price_cents", type: "integer" },
      { name: "discontinued", type: "boolean" },
    ],
  },
];

// What proves each scenario before it runs (PRD v7 §9.3b), as Vel lists them.
export const SAMPLE_SCENARIO_CHECKS = [
  {
    id: "setup", name: "setup.py", file: "scenarios/<id>/setup.py",
    desc: "Stages the state the scenario presumes: the rows, accounts and history it needs.",
    code: "def setup(world, scenario):\n    for row in scenario.presumes:\n        world.insert(row.table, row.values)\n    assert world.holds(scenario.presumes), \"world can't stage this scenario\"\n",
  },
  {
    id: "reference", name: "reference_solution", file: "scenarios/<id>/reference.py",
    desc: "A known-good run that must reach the expected end state.",
    code: "def reference(world, scenario):\n    trace = world.replay(scenario.reference_calls)\n    assert scenario.end_state.matches(world), \"reference solution doesn't solve it\"\n    return trace\n",
  },
  {
    id: "empty-run", name: "empty_run_fails", file: "scenarios/<id>/empty_run.py",
    desc: "Doing nothing must fail. Otherwise the scenario passes vacuously.",
    code: "def empty_run(world, scenario):\n    world.reset()\n    assert not scenario.end_state.matches(world), \"passes with no agent at all\"\n",
  },
  {
    id: "mutation", name: "mutation_controls", file: "scenarios/<id>/mutations.py",
    desc: "A wrong action fails the checks, and every check has a passing and a failing demonstration. No check that can't fail.",
    code: "def mutation_controls(world, scenario):\n    for mutant in scenario.wrong_actions():\n        assert not scenario.checks.pass_on(world.replay(mutant)), f\"{mutant} still passes\"\n    for check in scenario.checks:\n        assert check.demo_pass and check.demo_fail, f\"{check.id} can't fail\"\n",
  },
  {
    id: "altpaths", name: "alternate_paths_pass", file: "scenarios/<id>/alt_paths.py",
    desc: "A second valid solution, reached another way, passes too. The checks aren't overfit to one path.",
    code: "def alternate_paths(world, scenario):\n    for path in scenario.alternate_solutions:\n        world.reset()\n        assert scenario.checks.pass_on(world.replay(path)), f\"overfit: {path.order} fails\"\n",
  },
  {
    id: "setup-credit", name: "setup_earns_nothing", file: "scenarios/<id>/setup_credit.py",
    desc: "Nothing setup.py writes counts toward a check. Credit is only for the agent's own actions.",
    code: "def setup_earns_nothing(world, scenario):\n    staged = world.diff_since_setup()\n    for check in scenario.checks:\n        assert not check.credited_by(staged), f\"{check.id} counts setup state\"\n",
  },
  {
    id: "evidence", name: "positive_evidence", file: "scenarios/<id>/positive_evidence.py",
    desc: "Where declining is correct (a refusal, a guardrail, a transfer), a required utterance or a transfer call must appear. Replaces empty_run_fails there.",
    code: "def positive_evidence(world, scenario):\n    trace = world.replay([])\n    assert not scenario.checks.pass_on(trace), \"an empty run passes a refusal\"\n    ref = world.replay(scenario.reference_calls)\n    assert scenario.evidence.found_in(ref), \"no required utterance or transfer call\"\n",
  },
  {
    id: "exploit", name: "exploit_control", file: "scenarios/<id>/exploit_control.py",
    desc: "A scripted agent told to game the test (claiming success, talking to the judge or the caller) must score 0.",
    code: "def exploit_control(world, scenario):\n    trace = world.run(agent=ExploitAgent(scenario))\n    assert scenario.score(trace) == 0, \"an agent gaming the test scores\"\n",
  },
];

// Vel's Gateway → MCP servers, with the tools each lists.
const tool = (name, desc, readOnly, extra = {}) => ({ name, desc, readOnly, ...extra });
const SAMPLE_GATEWAY_TOOLS = {
  shopify: [
    tool("search_products", "Find products by title, SKU or tag.", true),
    tool("get_order", "An order with its line items, status and shipping address.", true),
    tool("create_refund", "Refund some or all of an order to the original payment method.", false, { destructive: true }),
    tool("update_shipping_address", "Change where an unfulfilled order ships.", false),
  ],
  stripe: [
    tool("list_charges", "Charges on a customer, newest first.", true),
    tool("get_customer", "A customer with their default payment method.", true),
    tool("create_refund", "Refund a charge, fully or in part.", false, { destructive: true }),
    tool("cancel_subscription", "Cancel a subscription now or at period end.", false, { destructive: true, blocked: true }),
  ],
  zendesk: [
    tool("search_tickets", "Tickets matching a requester, status or text.", true),
    tool("get_ticket", "A ticket with its comments and status.", true),
    tool("create_ticket", "Open a ticket for the requester.", false),
    tool("add_ticket_comment", "Replies default to public. Use reply_to_ticket, which asks.", false, { openWorld: true, deprecated: "reply_to_ticket" }),
    tool("reply_to_ticket", "Reply on a ticket; public or internal has to be said.", false, { openWorld: true }),
  ],
  calendar: [
    tool("list_events", "Events in a date range on a calendar.", true),
    tool("find_free_slots", "Open slots of a given length.", true),
    tool("create_event", "Book an event and invite attendees.", false, { openWorld: true }),
    tool("cancel_event", "Cancel an event and notify attendees.", false, { destructive: true, openWorld: true }),
  ],
};

export const SAMPLE_GATEWAY_SERVERS = [
  { id: "shopify", label: "Shopify", host: "mcp.shopify.com", status: "connected", toolCount: 4 },
  { id: "stripe", label: "Stripe", host: "mcp.stripe.com", status: "connected", toolCount: 4 },
  { id: "zendesk", label: "Zendesk", host: "acme.zendesk.com", status: "connected", toolCount: 5 },
  { id: "calendar", label: "Google Calendar", host: "calendarmcp.googleapis.com", status: "connected", toolCount: 4 },
  { id: "linear", label: "Linear", host: "mcp.linear.app", status: "error", error: "401 from the server, its token was revoked", checkedAgo: "4 minutes ago", toolCount: 0 },
];

export const sampleGatewayToolsOf = (id) => SAMPLE_GATEWAY_TOOLS[id] || [];
