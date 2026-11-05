/*
  Call records in the API's own shape — `WorkerTimeline` from platform PR
  #3699 (TH-8886, futureagi/simulate/types.py), copied from that PR's panel
  tests. Open a run's Participants tab with ?contract=<name> to see one.

  The contract still has one request and one reply per call; turn-by-turn
  records come with the next API change.
*/

const BASE = {
  collected_at: "2026-10-31T12:00:00Z",
  started_at: "2026-10-31T11:59:50",
  coordinator: { name: "Returns coordinator" },
  request: { text: "Please refund order 1042", at: "2026-10-31T11:59:54" },
  response: { text: "Your refund is on its way", at: "2026-10-31T12:00:10" },
  events_omitted: 0,
  spans_received: true,
};

const worker = (id, observed = true, extra = {}) => ({
  worker_id: `r/${id}`,
  id,
  name: `${id} worker`,
  observed,
  undeclared: false,
  instance_ids: [],
  ...extra,
});
const at = (second) => `2026-10-31T12:00:${String(second).padStart(2, "0")}`;
const numbered = (events) =>
  events.map((one, index) => ({
    attribution: "reported",
    ...one,
    seq: index + 1,
  }));

export const CONTRACT_SAMPLES = {
  /* The PR's main example: a failed tool call, a declared worker that sent
     nothing, and the coordinator's own call. */
  returns: {
    ...BASE,
    workers: [
      {
        worker_id: "r/order_lookup",
        id: "order_lookup",
        name: "Order-lookup worker",
        observed: true,
        undeclared: false,
        instance_ids: ["s3"],
      },
      {
        worker_id: "r/audit",
        id: "audit",
        name: "Audit worker",
        observed: false,
        undeclared: false,
        instance_ids: [],
      },
    ],
    events: [
      {
        seq: 1,
        type: "delegated",
        worker_id: "r/order_lookup",
        task: "inspect order 1042",
        at: "2026-10-31T11:59:55.900",
        attribution: "reported",
      },
      {
        seq: 2,
        type: "tool_call",
        worker_id: "r/order_lookup",
        tool: "get_order",
        status: "error",
        input: { order_id: "1042" },
        output: "store unavailable",
        at: "2026-10-31T11:59:57",
        attribution: "reported",
      },
      {
        seq: 3,
        type: "returned",
        worker_id: "r/order_lookup",
        result: "eligible",
        at: "2026-10-31T11:59:59",
        attribution: "reported",
      },
      {
        seq: 4,
        type: "tool_call",
        worker_id: null,
        tool: "send_reply",
        status: "ok",
        at: "2026-10-31T12:00:09",
        attribution: "reported",
      },
    ],
  },

  /* A hand-off under the worker that made it (ADK-style nesting). */
  nested: {
    ...BASE,
    workers: [worker("lead"), worker("helper")],
    events: numbered([
      { type: "delegated", worker_id: "r/lead", at: at(1) },
      { type: "delegated", worker_id: "r/helper", by: "r/lead", at: at(2) },
      { type: "returned", worker_id: "r/helper", result: "h", at: at(3) },
      { type: "returned", worker_id: "r/lead", result: "l", at: at(4) },
    ]),
  },

  /* Control passed to a worker, which then hands out two of its own. */
  transfer: {
    ...BASE,
    workers: [worker("inspiration"), worker("place"), worker("poi")],
    events: numbered([
      { type: "transferred", worker_id: "r/inspiration", by: null, at: at(1) },
      {
        type: "delegated",
        worker_id: "r/place",
        by: "r/inspiration",
        at: null,
      },
      { type: "returned", worker_id: "r/place", result: "places", at: at(5) },
      { type: "delegated", worker_id: "r/poi", by: "r/inspiration", at: null },
      { type: "returned", worker_id: "r/poi", result: "sights", at: at(9) },
    ]),
  },

  /* Two hand-offs whose spans overlap (LangGraph-style), then a third. */
  parallel: {
    ...BASE,
    workers: [worker("research"), worker("code"), worker("review")],
    events: numbered([
      { type: "delegated", worker_id: "r/research", task: "read", at: at(1) },
      { type: "delegated", worker_id: "r/code", task: "write", at: at(2) },
      { type: "returned", worker_id: "r/research", result: "notes", at: at(5) },
      { type: "returned", worker_id: "r/code", result: "patch", at: at(6) },
      { type: "delegated", worker_id: "r/review", task: "check", at: at(7) },
      { type: "returned", worker_id: "r/review", result: "ok", at: at(8) },
    ]),
  },

  /* A tool call no agent can be named for. */
  unattributed: {
    ...BASE,
    workers: [worker("order_lookup")],
    events: numbered([
      {
        type: "delegated",
        worker_id: "r/order_lookup",
        task: "inspect order 1042",
        at: at(1),
      },
      {
        type: "returned",
        worker_id: "r/order_lookup",
        result: "eligible",
        at: at(3),
      },
      {
        type: "tool_call",
        worker_id: null,
        tool: "get_order",
        status: "ok",
        input: { order_id: "1042" },
        output: { status: "delivered" },
        at: at(4),
        attribution: "unattributed",
      },
    ]),
  },

  /* A worker that ran without being declared, one still out when the call
     ended, and no traces from the app. */
  undeclared: {
    ...BASE,
    spans_received: false,
    response: null,
    workers: [
      worker("order_lookup"),
      worker("billing", true, { name: "Billing helper", undeclared: true }),
    ],
    events: numbered([
      {
        type: "delegated",
        worker_id: "r/order_lookup",
        task: "inspect order 1042",
        at: at(1),
      },
      {
        type: "tool_call",
        worker_id: "r/order_lookup",
        tool: "get_order",
        status: "ok",
        input: { order_id: "1042" },
        output: { status: "delivered" },
        at: at(2),
      },
      {
        type: "delegated",
        worker_id: "r/billing",
        task: "check the payment",
        at: at(3),
      },
    ]),
  },
};
