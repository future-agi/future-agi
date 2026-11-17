import { describe, it, expect } from "vitest";
import { participantsFromRecord } from "../participantsFromRecord";
import { sequenceOf, UNKNOWN_LANE, USER_LANE } from "../sequenceOf";
import {
  DELEGATED,
  LOOKUP,
  REPLY_CALL,
  callStep,
  handOffStep,
  oneTurn,
  prose,
  timeline,
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
      "order_lookup",
    ]);
    expect(seq.lanes[1].name).toBe("Returns coordinator");
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
    expect(tool.from).toBe("order_lookup");
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
    expect(tool.reason).toBe("not_permitted");
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

  it("a tool step the record ends takes its duration from the step's times", () => {
    const record = timeline({
      steps: [
        handOffStep(1, {
          worker_id: "r/order_lookup",
          handoff: DELEGATED,
          end: "no_return",
          children: [LOOKUP.seq],
        }),
        {
          ...callStep(LOOKUP, { parent: 1, depth: 1 }),
          ended_at: "2026-10-31T11:59:58.500000+00:00",
        },
      ],
      turns: oneTurn(undefined, undefined, [1]),
    });
    const tool = seqOf(record).steps.find((s) => s.tool === "get_order");
    expect(tool.dur).toBe(1.5);
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
    expect(loop).toMatchObject({ per: 2, times: 3, with: ["audit"] });
  });
});
