// A worker record as GET /simulate/v3/call-executions/{id}/workers/ serves it, for tests.

// The API's rows, written as it sends them: simulate/services/harness_worker_steps.py arranges a call's events
// into these, decides each hand-off's end and counts, and is tested there.
export const handOffStep = (
  key,
  {
    parent = null,
    depth = 1,
    worker_id,
    handoff = null,
    returned = null,
    end,
    children = [],
    parallel = false,
    relationship = "observed",
    did_not_finish = end === "failed" || end === "no_return",
    first_of_worker = true,
  },
) => ({
  kind: "handoff",
  key,
  parent,
  depth,
  worker_id,
  transfer: handoff?.type === "transferred",
  handoff,
  returned,
  end,
  did_not_finish,
  first_of_worker,
  children,
  parallel,
  turn: 1,
  order: key,
  relationship,
});
export const callStep = (call, { parent = null, depth = 0 } = {}) => ({
  kind: "tool_call",
  key: call.seq,
  parent,
  depth,
  worker_id: call.worker_id,
  call,
  turn: 1,
  order: call.seq,
  relationship: "observed",
});
export const oneTurn = (request, response, children, callsOutside = 0) => [
  {
    turn: 1,
    request,
    response,
    children,
    calls_outside: callsOutside,
    expects_messages: true,
  },
];
// Each turn's hand-offs as the API lists them: every hand-off step of that turn, in step order.
export const withHandOffs = (record) => ({
  ...record,
  turns: record.turns.map((round) => ({
    handoffs: record.steps
      .filter((step) => step.kind === "handoff" && step.turn === round.turn)
      .map((step) => step.key),
    ...round,
  })),
});
// A text as the API sends it, marked as prose or data.
export const prose = (text) => ({ format: "text", text });
export const counts = (finished, didNotFinish, extra) => ({
  hand_offs: finished + didNotFinish,
  workers: 1,
  finished,
  did_not_finish: didNotFinish,
  passed: 0,
  cut_off: 0,
  ...extra,
});

export const WORKERS = [
  {
    worker_id: "r/order_lookup",
    id: "order_lookup",
    name: "Order-lookup worker",
    observed: true,
    undeclared: false,
    instance_ids: ["s3"],
    cut_short: false,
    past_deadline: false,
    checks: [],
  },
  {
    worker_id: "r/audit",
    id: "audit",
    name: "Audit worker",
    observed: false,
    undeclared: false,
    instance_ids: [],
    cut_short: false,
    past_deadline: false,
    checks: [],
  },
];
export const REQUEST = {
  text: "Please refund order 1042",
  at: "2026-10-31T11:59:54+00:00",
  shown: prose("Please refund order 1042"),
};
export const RESPONSE = {
  text: "Your refund is on its way",
  at: "2026-10-31T12:00:10+00:00",
  shown: prose("Your refund is on its way"),
};
export const DELEGATED = {
  seq: 1,
  type: "delegated",
  worker_id: "r/order_lookup",
  delegation_id: "d1",
  task: "inspect order 1042",
  task_shown: prose("inspect order 1042"),
  at: "2026-10-31T11:59:55.900+00:00",
  attribution: "reported",
};
export const LOOKUP = {
  seq: 2,
  type: "tool_call",
  worker_id: "r/order_lookup",
  delegation_id: "d1",
  tool: "get_order",
  status: "error",
  input: { order_id: "1042" },
  output: "store unavailable",
  at: "2026-10-31T11:59:57+00:00",
  attribution: "reported",
};
export const RETURNED = {
  seq: 3,
  type: "returned",
  worker_id: "r/order_lookup",
  delegation_id: "d1",
  result: "eligible",
  result_shown: prose("eligible"),
  status: "ok",
  at: "2026-10-31T11:59:59+00:00",
  attribution: "reported",
};
export const REPLY_CALL = {
  seq: 4,
  type: "tool_call",
  worker_id: null,
  tool: "send_reply",
  status: "ok",
  at: "2026-10-31T12:00:09+00:00",
  attribution: "reported",
};

// One hand-off with a failed tool call and its return, then a call of the coordinator's own.
export const timeline = ({
  returned = RETURNED,
  end = "finished",
  request = REQUEST,
  response = RESPONSE,
  ...rest
} = {}) =>
  withHandOffs({
    collected_at: "2026-10-31T12:00:00Z",
    started_at: "2026-10-31T11:59:50+00:00",
    coordinator: { name: "Returns coordinator" },
    workers: WORKERS,
    events: [DELEGATED, LOOKUP, returned, REPLY_CALL].filter(Boolean),
    steps: [
      handOffStep(1, {
        worker_id: "r/order_lookup",
        handoff: DELEGATED,
        returned,
        end,
        children: [LOOKUP.seq],
      }),
      callStep(LOOKUP, { parent: 1, depth: 1 }),
      callStep(REPLY_CALL),
    ],
    turns: oneTurn(request, response, [1, REPLY_CALL.seq], 1),
    counts: counts(end === "finished" ? 1 : 0, end === "finished" ? 0 : 1),
    cut_off: false,
    beyond_boundary: false,
    placements: [],
    stopped_by_user: false,
    pending: false,
    state: "recorded",
    ...rest,
  });
