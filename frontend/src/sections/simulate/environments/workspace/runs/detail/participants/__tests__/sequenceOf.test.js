import { describe, it, expect } from "vitest";
import {
  participantsFromRecord,
  recordStart,
  recordingSeconds,
} from "../participantsFromRecord";
import { sequenceOf, UNKNOWN_LANE, USER_LANE } from "../sequenceOf";
import {
  DELEGATED,
  LOOKUP,
  REPLY_CALL,
  REQUEST,
  RETURNED,
  callStep,
  handOffStep,
  oneTurn,
  prose,
  timeline,
  WORKERS,
} from "../__fixtures__/workerRecord";

const seqOf = (record) =>
  sequenceOf(participantsFromRecord(record), { callerName: "Isabela" });

const ESCALATE = {
  seq: 5,
  type: "delegated",
  worker_id: "r/audit",
  task: "check the refund",
  at: "2026-10-31T11:59:56+00:00",
  attribution: "reported",
};

describe("sequenceOf over the worker record", () => {
  it("lanes run caller, coordinator, then workers in first-act order", () => {
    const seq = seqOf(timeline());
    expect(seq.lanes.map((lane) => lane.id)).toEqual([
      USER_LANE,
      "coordinator",
      "worker:order_lookup",
    ]);
    expect(seq.lanes[1].name).toBe("Returns coordinator");
  });

  it("keeps a participant whose id reads like the coordinator's on its own lane", () => {
    const seq = seqOf(
      timeline({
        workers: WORKERS.map((one) =>
          one.id === "order_lookup" ? { ...one, id: "coordinator" } : one,
        ),
      }),
    );
    expect(seq.lanes.map((lane) => lane.name)).toEqual([
      "Isabela",
      "Returns coordinator",
      "Order-lookup worker",
    ]);
  });

  it("numbers requests in start order and ties each reply to its request", () => {
    const seq = seqOf(timeline());
    expect(seq.steps.map((s) => [s.n, s.kind])).toEqual([
      [1, "message"],
      [2, "handoff"],
      [3, "tool"],
      [4, "tool"],
    ]);
    expect(seq.rows).toContainEqual(
      expect.objectContaining({ type: "back", n: 2 }),
    );
  });

  it("puts a tool call on the lane of the worker that made it", () => {
    const tool = seqOf(timeline()).steps.find((s) => s.tool === "get_order");
    expect(tool.from).toBe("worker:order_lookup");
    expect(tool.bad).toBe(true);
  });

  it("an unattributed tool call goes on the Unknown lane", () => {
    const loose = { ...REPLY_CALL, attribution: "unattributed" };
    const record = timeline({
      steps: [callStep(loose)],
      turns: oneTurn(undefined, undefined, [loose.seq]),
    });
    const tool = seqOf(record).steps.find((s) => s.tool === "send_reply");
    expect(tool.from).toBe(UNKNOWN_LANE);
  });

  it("a hand-off with no known sender comes from Unknown", () => {
    const record = timeline({
      steps: [
        handOffStep(5, {
          worker_id: "r/audit",
          handoff: ESCALATE,
          end: "no_return",
          relationship: "unknown",
        }),
      ],
      turns: oneTurn(undefined, undefined, [5]),
    });
    const handoff = seqOf(record).steps.find((s) => s.kind === "handoff");
    expect(handoff.from).toBe(UNKNOWN_LANE);
  });

  it("a hand-off with no return is a problem and has no answer", () => {
    const seq = seqOf(timeline({ returned: null, end: "no_return" }));
    const handoff = seq.steps.find((s) => s.kind === "handoff");
    expect(handoff.answer.kind).toBe("none");
    expect(handoff.bad).toBe(true);
    expect(seq.problems.map((p) => p.n)).toContain(handoff.n);
  });

  it("a refused tool call is refused, not failed, with the world's reason", () => {
    const refused = {
      ...LOOKUP,
      status: "error",
      refusal_code: "not_permitted",
    };
    const record = timeline({
      steps: [
        handOffStep(1, {
          worker_id: "r/order_lookup",
          handoff: DELEGATED,
          end: "finished",
          children: [refused.seq],
        }),
        callStep(refused, { parent: 1, depth: 1 }),
      ],
    });
    const tool = seqOf(record).steps.find((s) => s.tool === "get_order");
    expect(tool.refused).toBe(true);
    expect(tool.bad).toBe(false);
    // The world's code in plain words, never the code itself.
    expect(tool.reason).toBe("not permitted");
  });

  it("a refusal code it doesn't know reads only as refused", () => {
    const refused = {
      ...LOOKUP,
      status: "error",
      refusal_code: "some_new_code",
    };
    const record = timeline({
      steps: [
        handOffStep(1, {
          worker_id: "r/order_lookup",
          handoff: DELEGATED,
          end: "finished",
          children: [refused.seq],
        }),
        callStep(refused, { parent: 1, depth: 1 }),
      ],
    });
    const tool = seqOf(record).steps.find((s) => s.tool === "get_order");
    expect(tool.refused).toBe(true);
    expect(tool.reason).toBeNull();
  });

  it("a tool's payload dropped at the record's size limit says so, never that nothing came back", () => {
    const cut = { ...LOOKUP, output: null, output_omitted: true };
    const record = timeline({
      steps: [
        handOffStep(1, {
          worker_id: "r/order_lookup",
          handoff: DELEGATED,
          end: "finished",
          children: [cut.seq],
        }),
        callStep(cut, { parent: 1, depth: 1 }),
      ],
    });
    const tool = seqOf(record).steps.find((s) => s.tool === "get_order");
    expect(tool.result).toBe(
      "Not kept: this call's record reached its size limit",
    );
  });

  it("a hand-off the user stopped is neither an error nor a problem", () => {
    const record = timeline({
      steps: [
        {
          ...handOffStep(1, {
            worker_id: "r/order_lookup",
            handoff: DELEGATED,
            end: "no_return",
          }),
          end_state: "cancelled",
        },
      ],
      turns: oneTurn(undefined, undefined, [1]),
    });
    const seq = seqOf(record);
    const handoff = seq.steps.find((s) => s.kind === "handoff");
    expect(handoff.bad).toBe(false);
    expect(handoff.answer).toMatchObject({ kind: "none", bad: false });
    expect(seq.problems).toEqual([]);
  });

  it("attaches steps by the record's parent, never by time", () => {
    // The worker's own tool call 60 s in, outside any hand-off the record names.
    const late = { ...LOOKUP, seq: 9, at: "2026-10-31T12:00:50+00:00" };
    const record = timeline({
      steps: [
        handOffStep(1, {
          worker_id: "r/order_lookup",
          handoff: DELEGATED,
          returned: RETURNED,
          end: "finished",
        }),
        callStep(late),
      ],
      turns: oneTurn(undefined, undefined, [1, late.seq]),
    });
    const seq = seqOf(record);
    const handoff = seq.steps.find((s) => s.kind === "handoff");
    const tool = seq.steps.find((s) => s.tool === "get_order");
    expect(tool.parent).toBeNull();
    // Delegated 11:59:55.9, returned 11:59:59: what the record says, not stretched to the late call.
    expect(handoff.dur).toBeCloseTo(3.1, 1);
    expect(handoff.tools).toBe(0);
  });

  it("uses the served text as the exact message", () => {
    const shown = { ...DELEGATED, task_shown: prose("Inspect order 1042 now") };
    const record = timeline({
      steps: [
        handOffStep(1, {
          worker_id: "r/order_lookup",
          handoff: shown,
          end: "no_return",
        }),
      ],
      turns: oneTurn(undefined, undefined, [1]),
    });
    const handoff = seqOf(record).steps.find((s) => s.kind === "handoff");
    expect(handoff.full).toBe("Inspect order 1042 now");
    expect(handoff.label).toBe("Inspect order 1042 now");
  });

  it("keeps API order when times are missing", () => {
    const untimed = (event) => event && { ...event, at: null };
    const record = timeline({
      started_at: null,
      request: { text: "Please refund order 1042", at: null },
      response: { text: "Done", at: null },
      events: [],
      steps: [
        handOffStep(1, {
          worker_id: "r/order_lookup",
          handoff: untimed(DELEGATED),
          returned: { ...untimed(DELEGATED), type: "returned", result: "ok" },
          end: "finished",
          children: [LOOKUP.seq],
        }),
        callStep(untimed(LOOKUP), { parent: 1, depth: 1 }),
      ],
    });
    const seq = seqOf(record);
    expect(seq.steps.map((s) => s.kind)).toEqual([
      "message",
      "handoff",
      "tool",
    ]);
    expect(JSON.stringify(seq.steps)).not.toContain("NaN");
    expect(seq.steps.find((s) => s.kind === "handoff").answer.kind).toBe(
      "answer",
    );
  });

  it("a tool call without an end time has no duration", () => {
    const tool = seqOf(timeline()).steps.find((s) => s.tool === "get_order");
    expect(tool.durMs).toBeNull();
    expect(tool.dur).toBeNull();
  });

  it("a tool call the record ends takes its duration from the call's own times", () => {
    const record = timeline({
      steps: [
        handOffStep(1, {
          worker_id: "r/order_lookup",
          handoff: DELEGATED,
          end: "no_return",
          children: [LOOKUP.seq],
        }),
        callStep(
          { ...LOOKUP, ended_at: "2026-10-31T11:59:58.500000+00:00" },
          { parent: 1, depth: 1 },
        ),
      ],
      turns: oneTurn(undefined, undefined, [1]),
    });
    const tool = seqOf(record).steps.find((s) => s.tool === "get_order");
    expect(tool.dur).toBe(1.5);
  });

  it("a hand-off the record was cut before is not a failure and has no wait", () => {
    const seq = seqOf(timeline({ returned: null, end: "cut_off" }));
    const handoff = seq.steps.find((s) => s.kind === "handoff");
    expect(handoff.bad).toBe(false);
    expect(handoff.answer).toMatchObject({ kind: "none", bad: false });
    expect(handoff.dur).toBeNull();
    expect(seq.problems.map((p) => p.n)).not.toContain(handoff.n);
  });

  it("a hand-off with an end it doesn't know claims nothing, but shows a result the record has", () => {
    const silent = seqOf(timeline({ returned: null, end: "something_new" }));
    const handoff = silent.steps.find((s) => s.kind === "handoff");
    expect(handoff.answer).toBeNull();
    expect(handoff.bad).toBe(false);
    expect(handoff.dur).toBeNull();
    expect(silent.problems.map((p) => p.n)).not.toContain(handoff.n);
    const answered = seqOf(timeline({ end: "something_new" })).steps.find(
      (s) => s.kind === "handoff",
    );
    expect(answered.answer).toMatchObject({ kind: "answer", bad: false });
  });

  it("a hand-off with no answer and no end time shows no wait", () => {
    const handoff = seqOf(
      timeline({ returned: null, end: "no_return" }),
    ).steps.find((s) => s.kind === "handoff");
    expect(handoff.dur).toBeNull();
  });

  it("a caller's message whose reply has no time shows no duration", () => {
    const record = timeline({ response: { text: "Done", at: null } });
    const message = seqOf(record).steps.find((s) => s.kind === "message");
    expect(message.dur).toBeNull();
  });

  it("a round the record gives no turn keeps no turn number", () => {
    const record = timeline();
    record.turns = [
      ...record.turns,
      {
        ...record.turns[0],
        turn: null,
        request: null,
        response: null,
        children: [REPLY_CALL.seq],
      },
    ];
    record.turns[0].children = [1];
    const tool = seqOf(record).steps.find((s) => s.tool === "send_reply");
    expect(tool.turn).toBeNull();
  });

  it("a worker's tool call with no known hand-off goes on the worker's lane, under no hand-off", () => {
    const loose = { ...REPLY_CALL, worker_id: "r/order_lookup" };
    const record = timeline({
      steps: [
        handOffStep(1, {
          worker_id: "r/order_lookup",
          handoff: DELEGATED,
          end: "no_return",
        }),
        { ...callStep(loose), relationship: "unknown" },
      ],
      turns: oneTurn(undefined, undefined, [1, loose.seq]),
    });
    const tool = seqOf(record).steps.find((s) => s.tool === "send_reply");
    expect(tool.from).toBe("worker:order_lookup");
    expect(tool.parent).toBeNull();
  });

  it("puts a record time on the recording's clock from the record's own start", () => {
    const record = timeline({ started_at: null });
    const zero = recordStart(record);
    expect(zero).toBe(REQUEST.at);
    // The request came 4 s after the call started, so record second 2 is recording second 6.
    expect(recordingSeconds(2, zero, "2026-10-31T11:59:50+00:00")).toBeCloseTo(
      6,
    );
  });

  it("keeps the API step key and the span to open", () => {
    const traced = { ...DELEGATED, span_id: "s1", trace_id: "t1" };
    const record = timeline({
      steps: [
        handOffStep(1, {
          worker_id: "r/order_lookup",
          handoff: traced,
          end: "no_return",
        }),
      ],
      turns: oneTurn(undefined, undefined, [1]),
    });
    const handoff = seqOf(record).steps.find((s) => s.kind === "handoff");
    expect(handoff).toMatchObject({ key: 1, spanId: "s1", traceId: "t1" });
  });

  it("a repeated round names its other workers, not their messages", () => {
    const rounds = [0, 1, 2].flatMap((i) => [
      handOffStep(10 + i * 2, {
        worker_id: "r/order_lookup",
        handoff: { ...DELEGATED, seq: 10 + i * 2, at: null },
        returned: { ...DELEGATED, type: "returned", result: "ok", at: null },
        end: "finished",
      }),
      handOffStep(11 + i * 2, {
        worker_id: "r/audit",
        handoff: {
          ...DELEGATED,
          seq: 11 + i * 2,
          worker_id: "r/audit",
          task: "check the order carefully",
          at: null,
        },
        returned: { ...DELEGATED, type: "returned", result: "ok", at: null },
        end: "finished",
      }),
    ]);
    const record = timeline({
      started_at: null,
      request: { text: "Please refund order 1042", at: null },
      response: { text: "Done", at: null },
      steps: rounds,
      turns: oneTurn(
        { text: "Please refund order 1042", at: null },
        { text: "Done", at: null },
        rounds.map((s) => s.key),
      ),
    });
    const [loop] = seqOf(record).loops;
    expect(loop).toMatchObject({ per: 2, times: 3, with: ["worker:audit"] });
  });
});
