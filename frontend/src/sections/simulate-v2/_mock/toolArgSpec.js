/*
  What each tool argument is and allows — one spec for both places that show
  it: the handler code on World → Tools (`envInternals.toolImplFor`) and the
  argument list and limits on the Contract tab (`rlContract.toolArgsOf`).
  By tool and argument where it differs, else by argument name.
*/
const ARG_SPEC = {
  order_id: { type: "str", format: "A-#####" },
  amount: { type: "float", min: 0, max: "order total", unit: "£" },
  amount_cents: { type: "int", min: 0, max: "order total", unit: "pence" },
  caller_confirmed: { type: "bool", values: ["true"], note: "must be true" },
  reason: {
    type: "enum",
    values: ["damaged", "late", "not_as_described", "changed_mind"],
  },
  priority: { type: "enum", values: ["normal", "urgent"] },
  factors: {
    type: "enum",
    values: ["dob", "card_last4", "otp", "postcode"],
    note: "one or more",
  },
  kind: { type: "enum", values: ["meal", "hotel"] },
  phone: { type: "str", format: "E.164 number" },
  postcode: { type: "str", format: "postcode" },
  code: { type: "str", format: "6 digits" },
  email: { type: "str", format: "email address" },
  since: { type: "date" },
  when: { type: "date" },
  new_address: { type: "object" },
  action: { type: "object" },
};
const TOOL_ARG_SPEC = {
  "escalate_to_human.reason": {
    type: "enum",
    values: [
      "refund_over_limit",
      "dispute",
      "vulnerable_caller",
      "caller_request",
    ],
  },
  "raise_dispute.reason": {
    type: "enum",
    values: ["unrecognised", "duplicate", "not_received"],
  },
  "apply_goodwill_credit.amount": {
    type: "float",
    min: 0,
    max: 25,
    unit: "£",
    note: "per call — the rules cap goodwill credit",
  },
  "issue_voucher.amount_cents": {
    type: "int",
    min: 0,
    max: 15000,
    unit: "pence",
  },
};

/** The spec for one argument of one tool — `{}` when nothing is known beyond its name. */
export const argSpecOf = (tool, name) => ({
  ...ARG_SPEC[name],
  ...TOOL_ARG_SPEC[`${tool}.${name}`],
});

/* The Python type a handler declares for an argument of that spec. */
const PY_OF = {
  str: "str",
  int: "int",
  float: "float",
  bool: "bool",
  enum: "str",
  date: "str",
  object: "dict",
};
export const pyTypeOf = (tool, name) =>
  PY_OF[argSpecOf(tool, name).type] || "str";

/* ── what a tool returns, reads and changes (world.md, Tool purpose and contract) ── */

/* Output fields by tool name, first match wins; the same shapes the handler
   code on World → Tools returns. */
const OUT_SPEC = [
  [
    /verify|check_otp/,
    [
      ["verified", "bool"],
      ["customer_id", "text"],
    ],
  ],
  [
    /refund/,
    [
      ["refund_id", "text"],
      ["status", "text"],
      ["amount", "number"],
    ],
  ],
  [
    /escalate/,
    [
      ["ticket_id", "text"],
      ["queue", "text"],
    ],
  ],
  [
    /voucher/,
    [
      ["voucher_id", "text"],
      ["amount_cents", "number"],
    ],
  ],
  [
    /freeze_card/,
    [
      ["card_id", "text"],
      ["status", "text"],
    ],
  ],
  [
    /dispute/,
    [
      ["dispute_id", "text"],
      ["status", "text"],
    ],
  ],
  [
    /rebook/,
    [
      ["booking_ref", "text"],
      ["flight_number", "text"],
      ["status", "text"],
    ],
  ],
  [
    /check_compensation/,
    [
      ["eligible", "bool"],
      ["amount_cents", "number"],
    ],
  ],
  [
    /return_eligibility|check_return/,
    [
      ["eligible", "bool"],
      ["window_closes", "date"],
    ],
  ],
  [
    /return_label/,
    [
      ["label_url", "text"],
      ["expires", "date"],
    ],
  ],
  [
    /cancel/,
    [
      ["order_id", "text"],
      ["status", "text"],
    ],
  ],
  [
    /update_shipping/,
    [
      ["order_id", "text"],
      ["address", "object"],
    ],
  ],
  [
    /shipment/,
    [
      ["carrier", "text"],
      ["tracking_number", "text"],
      ["status", "text"],
    ],
  ],
  [/list_transactions/, [["transactions", "list"]]],
  [/search_flights/, [["flights", "list"]]],
  [
    /lookup_booking/,
    [
      ["reference", "text"],
      ["status", "text"],
      ["flight_number", "text"],
    ],
  ],
  [
    /order/,
    [
      ["order_id", "text"],
      ["status", "text"],
      ["total", "number"],
    ],
  ],
  [
    /browser/,
    [
      ["ok", "bool"],
      ["url", "text"],
    ],
  ],
];

/** The fields a tool returns — `[{ name, type }]`; a sensible default by effect when the name says nothing. */
export const toolOutputOf = (tool, effect) => {
  const hit = OUT_SPEC.find(([re]) =>
    re.test(String(tool?.name || "").toLowerCase()),
  );
  const fields = hit
    ? hit[1]
    : effect === "write"
      ? [
          ["ok", "bool"],
          ["id", "text"],
        ]
      : [["rows", "list"]];
  return fields.map(([name, type]) => ({ name, type }));
};

/* Which seeded table a tool is about: [tool name, table name]. */
const TABLE_OF = [
  [/refund/, /refund/],
  [/return/, /return/],
  [/shipment|shipping/, /shipment|order/],
  [/order|cancel/, /order/],
  [/booking|rebook|compensation|voucher/, /booking/],
  [/transaction|dispute|freeze|card/, /transaction|card|account/],
  [/flight/, /flight/],
  [/verify|identity|otp/, /customer|account|user/],
];

const stem = (name) => String(name || "").replace(/s$/, "");

/* The seeded table (of `tables`, a list of names) a tool is about, or null. */
const primaryTableOf = (name, tables) => {
  const n = String(name || "").toLowerCase();
  for (const [toolRe, tableRe] of TABLE_OF) {
    if (!toolRe.test(n)) continue;
    const hit = tables.find((t) => tableRe.test(t.toLowerCase()));
    if (hit) return hit;
  }
  return tables.find((t) => n.includes(stem(t.toLowerCase()))) || null;
};

/**
 * The tables a tool reads and the ones it changes, given the environment's
 * seeded table names. A read-only tool changes none; a write changes one.
 * A tool that runs for real (shell, browser) touches none of the world's.
 */
export const toolTablesOf = (name, effect, tables) => {
  if (effect === "real") return { reads: [], changes: [] };
  const primary = primaryTableOf(name, tables) || tables[0] || null;
  if (!primary) return { reads: [], changes: [] };
  return effect === "write"
    ? { reads: [], changes: [primary] }
    : { reads: [primary], changes: [] };
};

/* The field a write touches and how it reads before and after, by tool name. */
const CHANGE_SPEC = [
  [/refund/, "refund_status", "none", "issued"],
  [/cancel/, "status", "confirmed", "cancelled"],
  [/freeze/, "status", "active", "frozen"],
  [/dispute/, "dispute_status", "none", "open"],
  [/rebook/, "flight_number", "original flight", "new flight"],
  [/voucher/, "voucher_cents", "0", "issued"],
  [/escalate/, "owner", "agent", "human queue"],
  [/shipping|address/, "address", "previous address", "new address"],
  [/update/, "status", "open", "updated"],
  [
    /create|book|send|submit|post|charge|transfer|generate/,
    "status",
    "no record",
    "created",
  ],
];

/** `{ field, before, after }` — what one committed write does to its record. */
export const toolChangeOf = (name) => {
  const hit = CHANGE_SPEC.find(([re]) =>
    re.test(String(name || "").toLowerCase()),
  );
  return hit
    ? { field: hit[1], before: hit[2], after: hit[3] }
    : { field: "status", before: "open", after: "updated" };
};
