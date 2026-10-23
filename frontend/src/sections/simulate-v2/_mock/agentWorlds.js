/*
  The world a build reads from an agent.

  Building from an agent reads what it does — its name, its prompt, the tools
  its traces call, the Observe project it reports to — and builds the world
  those tools act on. A rebooking agent gets bookings, flights and fares, not
  a storefront's orders and refunds: a world that can't answer the agent's own
  tools can't reproduce its failures, so a fix proven there proves nothing.

  Each world is what the builder would have read: tools with their argument
  names, five hard rules, the seeded tables (with the awkward rows that make
  them worth testing), and the lines the build narrates. The storefront
  support line is the fallback when nothing names a domain — and the world
  the seeded demo was built in, unchanged.

  `worldFor(source)` picks one from a build source (buildPresetFor's output,
  a repo or platform connect, a pasted prompt).
*/

import { listMcpTools, worldToolFromMcp } from "./mcpServers";

const SUPPORT = {
  id: "support",
  domain: "support",
  description:
    "Phone support for an online storefront. Callers chase deliveries, ask for refunds and dispute charges; the agent resolves inside policy or hands over.",
  tools: [
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
  ],
  rules: [
    "Never issue a refund outside the return window without a supervisor.",
    "Never read back more than the last four digits of a card.",
    "Verify identity before disclosing or changing anything on an account.",
    "An OTP must be read aloud by the caller — never inferred or guessed.",
    "Goodwill credit is capped at £25 per call.",
  ],
  seed: [
    { name: "customers", rows: 240, note: "40 with saved cards, 12 guests" },
    { name: "orders", rows: 610, note: "18% delivered outside the window" },
    { name: "returns", rows: 95, note: "22 excluded items" },
    { name: "payments", rows: 480, note: "9 expired cards" },
    { name: "policies", rows: 12, note: "window, exclusions, credit cap" },
  ],
  edges: "18% of orders outside the window · 22 excluded items · 9 expired cards · 12 guests",
  sampleTool: "issue_refund",
  proof: "the order is actually cancelled",
};

const TRAVEL = {
  id: "travel",
  domain: "travel",
  description:
    "Chat for an airline's rebooking desk. Travellers change dates, chase cancelled segments and ask what they're owed; the agent rebooks inside the fare and delay rules or hands over.",
  tools: [
    { name: "verify_passenger", args: ["booking_ref", "last_name"], desc: "Match the traveller to a booking before changing it." },
    { name: "lookup_booking", args: ["booking_ref"], desc: "Fetch a booking, its segments and the fare paid." },
    { name: "search_flights", args: ["origin", "destination", "date"], desc: "Alternatives on the route for the date asked — a new date needs a new search." },
    { name: "get_fare_quote", args: ["flight_id", "fare_class"], desc: "Price one flight before anything is promised." },
    { name: "rebook_segment", args: ["booking_ref", "segment_id", "flight_id", "passenger_confirmed"], desc: "Move a traveller to another flight." },
    { name: "cancel_segment", args: ["booking_ref", "segment_id"], desc: "Cancel one segment, keeping the rest of the trip." },
    { name: "check_compensation", args: ["booking_ref", "segment_id"], desc: "Entitlement under the delay rules." },
    { name: "issue_voucher", args: ["booking_ref", "kind", "amount"], desc: "Meal or hotel voucher during a disruption." },
    { name: "send_itinerary", args: ["booking_ref", "email"], desc: "Email the updated itinerary." },
    { name: "escalate_to_human", args: ["reason"], desc: "Hand the chat to a person." },
  ],
  rules: [
    "Confirm the booking reference and the traveller's name before changing a booking.",
    "State the fare difference from a current quote before confirming any rebooking.",
    "Never promise compensation before check_compensation returns eligible.",
    "Offer same-day alternatives before discussing a refund.",
    "Vouchers are capped at €40 for a meal and €150 for a hotel per traveller.",
  ],
  seed: [
    { name: "bookings", rows: 900, note: "140 on cancelled flights" },
    { name: "flights", rows: 260, note: "18 cancelled, 34 delayed over 3h" },
    { name: "fares", rows: 1200, note: "9% priced differently a day either side" },
    { name: "fare_rules", rows: 45, note: "12 non-refundable" },
    { name: "compensation_claims", rows: 75, note: "22 already paid" },
  ],
  edges: "140 bookings on cancelled flights · 9% of fares priced differently a day either side · 12 non-refundable fares · 22 claims already paid",
  sampleTool: "rebook_segment",
  proof: "the traveller is actually on the new flight",
};

const BILLING = {
  id: "billing",
  domain: "fintech",
  description:
    "Billing and account support. Customers query charges, dispute transactions and change how they pay; the agent verifies, explains and resolves inside policy or hands over.",
  tools: [
    { name: "verify_identity", args: ["customer_id", "date_of_birth"], desc: "Match the customer to an account before reading anything out." },
    { name: "send_otp", args: [], desc: "Text a one-time code to the number on file." },
    { name: "check_otp", args: ["code"], desc: "Verify a code the customer reads back. Never inferred." },
    { name: "list_transactions", args: ["account_id", "from", "to"], desc: "Statement search for a date range." },
    { name: "get_invoice", args: ["invoice_id"], desc: "An invoice with its lines and payments." },
    { name: "explain_charge", args: ["transaction_id"], desc: "What a charge was for, from the ledger." },
    { name: "raise_dispute", args: ["transaction_id", "reason", "customer_confirmed"], desc: "Open a dispute on one transaction." },
    { name: "freeze_card", args: ["card_id", "reason"], desc: "Block a card at once." },
    { name: "issue_credit", args: ["account_id", "amount", "reason"], desc: "Credit the account, capped by policy." },
    { name: "update_payment_method", args: ["account_id", "method_token"], desc: "Change how the account pays." },
    { name: "escalate_to_human", args: ["reason"], desc: "Hand the conversation to a person." },
  ],
  rules: [
    "Two identity factors before any account detail is read out.",
    "Never read back more than the last four digits of a card.",
    "Raise a dispute only on a transaction the customer names and confirms.",
    "Credits above $50 need a supervisor.",
    "Always state the dispute window — 10 business days.",
  ],
  seed: [
    { name: "accounts", rows: 400, note: "30 frozen, 12 joint" },
    { name: "transactions", rows: 15000, note: "180 flagged as suspicious" },
    { name: "invoices", rows: 2300, note: "6% with a duplicate line" },
    { name: "cards", rows: 520, note: "45 reported lost" },
    { name: "disputes", rows: 140, note: "35 past SLA" },
  ],
  edges: "180 flagged transactions · 6% of invoices with a duplicate line · 45 lost cards · 35 disputes past SLA",
  sampleTool: "raise_dispute",
  proof: "the dispute is actually open on the right transaction",
};

const BOOKINGS = {
  id: "bookings",
  domain: "booking",
  description:
    "Front desk for appointments and reservations. Callers book, move and cancel; the agent finds a real slot, reads it back and keeps to the cancellation rules or hands over.",
  tools: [
    { name: "find_record", args: ["phone_or_email"], desc: "Find the caller's record." },
    { name: "verify_caller", args: ["name", "date_of_birth_or_ref"], desc: "Confirm who's calling before changing a booking." },
    { name: "search_availability", args: ["service", "from_date", "to_date"], desc: "Open slots for a service in a date range." },
    { name: "hold_slot", args: ["slot_id"], desc: "Hold a slot for a few minutes while the caller decides." },
    { name: "book_appointment", args: ["record_id", "slot_id", "caller_confirmed"], desc: "Book a held slot." },
    { name: "reschedule_appointment", args: ["appointment_id", "slot_id"], desc: "Move a booking to another slot." },
    { name: "cancel_appointment", args: ["appointment_id", "reason"], desc: "Cancel, applying the late-cancellation rule." },
    { name: "get_policy", args: ["topic"], desc: "Cancellation, deposit and preparation rules." },
    { name: "send_confirmation", args: ["appointment_id", "channel"], desc: "Text or email the confirmation." },
    { name: "add_to_waitlist", args: ["record_id", "service"], desc: "Offer the next opening that comes up." },
    { name: "escalate_to_human", args: ["reason"], desc: "Hand the call to a person." },
  ],
  rules: [
    "Read the date and time back before confirming any booking.",
    "Cancelling inside 24 hours carries the late fee — say so before cancelling.",
    "Verify the caller before changing an existing booking.",
    "Never share another person's booking or record.",
    "Anything urgent or safety-related goes to a person straight away.",
  ],
  seed: [
    { name: "records", rows: 1100, note: "60 with two records under one phone" },
    { name: "slots", rows: 3400, note: "8% double-held" },
    { name: "bookings", rows: 780, note: "45 inside the 24-hour window" },
    { name: "waitlist", rows: 120, note: "30 already offered a slot" },
    { name: "policies", rows: 9, note: "late fee, deposits, preparation" },
  ],
  edges: "60 duplicate records · 8% of slots double-held · 45 bookings inside the 24-hour window · 30 waitlist entries already offered",
  sampleTool: "book_appointment",
  proof: "the slot is actually booked under the right record",
};

const DISPATCH = {
  id: "dispatch",
  domain: "logistics",
  description:
    "Dispatch for pickups. Customers book, change and chase rides; the agent quotes, books and tracks inside the fare and cancellation rules or hands over.",
  tools: [
    { name: "verify_customer", args: ["phone"], desc: "Match the caller to an account." },
    { name: "quote_fare", args: ["pickup_address", "dropoff_address", "time"], desc: "Price a trip before it's booked." },
    { name: "book_pickup", args: ["pickup_address", "dropoff_address", "time", "customer_confirmed"], desc: "Book the trip." },
    { name: "update_pickup_address", args: ["booking_id", "address"], desc: "Change where the driver goes." },
    { name: "assign_driver", args: ["booking_id"], desc: "Put a driver on the booking." },
    { name: "track_driver", args: ["booking_id"], desc: "Where the driver is and when they'll arrive." },
    { name: "cancel_pickup", args: ["booking_id", "reason"], desc: "Cancel, applying the fee once a driver is assigned." },
    { name: "apply_credit", args: ["booking_id", "amount"], desc: "Credit a trip, capped by policy." },
    { name: "escalate_to_human", args: ["reason"], desc: "Hand the call to a person." },
  ],
  rules: [
    "Quote the fare before booking, and again if the address or time changes.",
    "Read the pickup address and time back before booking.",
    "Cancelling after a driver is assigned carries the fee — say so first.",
    "Never share a driver's phone number or location history.",
    "Credits are capped at $15 per trip.",
  ],
  seed: [
    { name: "bookings", rows: 1800, note: "7% with the address changed after booking" },
    { name: "drivers", rows: 140, note: "12 going off shift mid-booking" },
    { name: "fares", rows: 600, note: "surge on 15% of quotes" },
    { name: "zones", rows: 24, note: "3 airport zones at a flat rate" },
  ],
  edges: "7% of bookings with the address changed after booking · 12 drivers going off shift · surge on 15% of quotes · 3 flat-rate airport zones",
  sampleTool: "book_pickup",
  proof: "the driver is actually going to the new address",
};

export const AGENT_WORLDS = [SUPPORT, TRAVEL, BILLING, BOOKINGS, DISPATCH];

/* First match wins — travel before bookings, so a rebooking desk isn't read
   as a front desk. */
const MATCHERS = [
  [TRAVEL, /\b(travel|rebook\w*|flights?|airlines?|airport|itinerar\w*|search flights)\b/],
  [DISPATCH, /\b(pickups?|dispatch\w*|couriers?|rides?|taxi|book pickup|quote fare)\b/],
  [BILLING, /\b(bank\w*|billing|bills?|invoices?|disputes?|chargebacks?|raise dispute)\b/],
  [BOOKINGS, /\b(appointments?|clinic|medical|patients?|dental|hotel|guests?|reservations?|front desk|salon)\b/],
];

const toolNames = (list) => (Array.isArray(list) ? list.map((t) => (typeof t === "string" ? t : t?.name)) : []);
const flat = (parts) => parts.filter(Boolean).join(" ").toLowerCase().replace(/[-_/.]+/g, " ");

/* What names the agent — its name, the environment's, its repository, its
   Observe project, the tools it calls. A prompt mentions everything ("refund
   to the card on file"), so it's read only when none of these name a domain. */
const namesOf = (source) => {
  const agent = source?.connectedAgent || null;
  return flat([
    source?.agentName, source?.name, source?.envName, source?.value, source?.repoUrl,
    source?.observeProject?.id, source?.observeProject?.name,
    agent?.name, agent?.values?.observability?.project, agent?.definition?.repoUrl,
    ...toolNames(source?.tools), ...toolNames(agent?.definition?.tools),
  ]);
};
const promptOf = (source) => flat([source?.systemPrompt, source?.prompt, source?.connectedAgent?.definition?.prompt]);

const match = (text) => MATCHERS.find(([, re]) => re.test(text))?.[0] || null;

/**
 * The world a build reads from this source — the storefront line when nothing
 * names a domain. A build that starts from a world the user picked (a
 * prebuilt world) uses that world instead of reading one from the agent.
 */
export const worldFor = (source) => {
  if (Array.isArray(source?.worldSource?.world?.tools)) return source.worldSource.world;
  const read = match(namesOf(source)) || match(promptOf(source)) || SUPPORT;
  /* An MCP server as the world: its tools are what the agent acts on, one
     to one; the tables they answer from are still the agent's domain. */
  if (source?.worldSource?.kind === "mcp") {
    const listed = listMcpTools(source.worldSource);
    if (listed.ok) return { ...read, tools: listed.tools.map((t) => worldToolFromMcp(t, listed.server)) };
  }
  return read;
};

/**
 * A prebuilt world in the shape a build reads: its tables, tools and rules as
 * designed, so an agent's environment can start from it.
 */
export const worldFromTemplate = (template) => {
  const tables = template?.seed?.tables || [];
  const tools = (template?.tools || []).map((t) => ({ name: t.name, args: t.args || [], desc: t.desc || "" }));
  const notes = tables.filter((t) => t.note).map((t) => `${t.name}: ${t.note}`);
  return {
    id: `world-${template?.id || "prebuilt"}`,
    domain: template?.domain || "support",
    description: template?.description || template?.tagline || "",
    tools,
    rules: [...(template?.rules || [])],
    seed: tables.map((t) => ({ name: t.name, rows: t.rows, note: t.note || "" })),
    edges: notes.join(" · ") || "the rows the world was seeded with",
    sampleTool: (tools.find((t) => t.args.length) || tools[0])?.name || null,
    proof: "the change actually landed in the world's records",
  };
};

/**
 * The world's tools, with any the agent itself was read to call (a repo's
 * "Here's what we found", a framework's traces) added — so the world answers
 * every tool the agent calls, not only the ones the domain usually has.
 */
export const worldToolsOf = (world, source) => {
  const own = [...(source?.tools || []), ...(source?.connectedAgent?.definition?.tools || [])]
    .map((t) => (typeof t === "string" ? { name: t } : t))
    .filter((t) => t?.name);
  const have = new Set(world.tools.map((t) => t.name));
  const extra = own
    .filter((t, i, list) => !have.has(t.name) && list.findIndex((x) => x.name === t.name) === i)
    .map((t) => ({ name: t.name, args: t.args || [], desc: t.desc || "Read from the agent." }));
  /* Where a tool came from, and whether it only reads, travel with it. */
  return [...world.tools, ...extra].map((t) => ({ name: t.name, desc: t.desc, args: t.args, ...(t.effect && { effect: t.effect }), ...(t.via && { via: t.via }) }));
};

export const seededRowsOf = (world) => world.seed.reduce((n, s) => n + s.rows, 0);
