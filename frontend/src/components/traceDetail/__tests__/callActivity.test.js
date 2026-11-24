import { describe, expect, it } from "vitest";
import {
  buildCallActivity,
  turnWallMs,
  utcMs,
  withTurnIds,
  MAX_ACTIVITY,
} from "../callActivity";

const T0 = Date.parse("2026-11-24T10:00:00.000Z");
const at = (ms) => new Date(T0 + ms).toISOString();
const span = (id, extra = {}, children = []) => ({
  observation_span: {
    id,
    name: id,
    observation_type: "unknown",
    start_time: at(0),
    end_time: at(7000),
    ...extra,
  },
  children,
});
const tool = (id, start, end, extra = {}) =>
  span(id, {
    observation_type: "tool",
    start_time: at(start),
    end_time: end === null ? null : at(end),
    input: { secret: "canary-in" },
    output: { secret: "canary-out" },
    ...extra,
  });
// LiveKit shape: tools live under agent_session, a child of the call root.
const call = (tools, rootExtra = {}) => [
  span("root", { observation_type: "conversation", ...rootExtra }, [
    span("session", {}, tools),
  ]),
];
const absolute = [
  { role: "user", content: "Is my order on its way?", timestamp: at(500) },
  {
    role: "assistant",
    content: "I'm checking your order.",
    timestamp: at(2500),
  },
  { role: "assistant", content: "It shipped today.", timestamp: at(4500) },
];

describe("clock helpers", () => {
  it("reads zone-less API times as UTC", () => {
    expect(utcMs("2026-11-24T10:00:00.000")).toBe(T0);
    expect(utcMs("2026-11-24 10:00:00")).toBe(T0);
    expect(utcMs("2026-11-24T10:00:00Z")).toBe(T0);
    expect(utcMs("2026-11-24T15:30:00+05:30")).toBe(T0);
    expect(utcMs("")).toBeNull();
    expect(utcMs(42)).toBeNull();
    // Not a stated instant, whatever Date.parse would make of it.
    expect(utcMs("12/31")).toBeNull();
    expect(utcMs("2026-11-24")).toBeNull();
    expect(utcMs("Tue Nov 24 2026")).toBeNull();
  });

  it("finds a stated wall clock, never one built from call-relative seconds", () => {
    expect(turnWallMs({ timestamp: at(500) })).toBe(T0 + 500);
    expect(turnWallMs({ start_time_seconds: 0.5, time: at(500) })).toBe(
      T0 + 500,
    );
    expect(turnWallMs({ start_time: T0 + 500 })).toBe(T0 + 500);
    expect(turnWallMs({ startTimeSeconds: (T0 + 500) / 1000 })).toBe(T0 + 500);
    expect(turnWallMs({ start_time_seconds: 0.5 })).toBeNull();
    expect(turnWallMs({ start: "2.5" })).toBeNull();
    expect(turnWallMs({ timeStamp: at(500) })).toBe(T0 + 500);
    expect(turnWallMs({ time: "12/31" })).toBeNull();
    expect(turnWallMs({})).toBeNull();
  });
});

describe("withTurnIds", () => {
  it("keeps supplied ids and never generates one that repeats them", () => {
    const rows = [
      { id: "call-turn-1", role: "user", content: "a" },
      { role: "assistant", content: "b" },
      { id: 7, role: "user", content: "c" },
      { role: "assistant", content: "d" },
    ];
    const ids = withTurnIds(rows).map((row) => row.id);
    expect(ids).toEqual(["call-turn-1", "call-turn-1-1", 7, "call-turn-3"]);
    expect(new Set(ids.map(String)).size).toBe(rows.length);
    expect(withTurnIds(rows)[0]).toBe(rows[0]);
    expect(withTurnIds(undefined)).toEqual([]);
  });
});

describe("buildCallActivity", () => {
  it("places a tool between the utterances around it on the shared wall clock", () => {
    const { transcript, activity } = buildCallActivity({
      tree: call([tool("lookup", 3000, 4000)]),
      rootSpanId: "root",
      transcript: absolute,
    });
    expect(transcript.map((t) => t.id)).toEqual([
      "call-turn-0",
      "call-turn-1",
      "call-turn-2",
    ]);
    expect(activity).toEqual([
      {
        key: "lookup",
        spanId: "lookup",
        name: "lookup",
        mcp: false,
        failed: false,
        startMs: 3000,
        endMs: 4000,
        interleaved: true,
        anchorTurnId: "call-turn-2",
      },
    ]);
  });

  it("keeps speech first on a tie and puts a late tool after the last utterance", () => {
    const { activity } = buildCallActivity({
      tree: call([tool("tie", 2500, 2600), tool("late", 6500, 6800)]),
      rootSpanId: "root",
      transcript: absolute,
    });
    expect(activity.map((a) => [a.key, a.anchorTurnId])).toEqual([
      ["tie", "call-turn-2"],
      ["late", null],
    ]);
  });

  it("never places tools among call-relative utterances: they form their own group", () => {
    const relative = absolute.map(({ timestamp, ...t }, i) => ({
      ...t,
      start_time_seconds: [0.5, 2.5, 4.5][i],
    }));
    const { activity } = buildCallActivity({
      tree: call([tool("lookup", 3000, 4000)]),
      rootSpanId: "root",
      transcript: relative,
    });
    expect(activity).toHaveLength(1);
    expect(activity[0]).toMatchObject({ interleaved: false, startMs: 3000 });
    expect(activity[0]).not.toHaveProperty("anchorTurnId");
  });

  it("groups everything when one utterance lacks a wall clock or there is no speech", () => {
    const mixed = [
      ...absolute.slice(0, 2),
      { role: "assistant", content: "x" },
    ];
    for (const transcript of [mixed, [], undefined]) {
      const { activity } = buildCallActivity({
        tree: call([tool("lookup", 3000, 4000)]),
        rootSpanId: "root",
        transcript,
      });
      expect(activity.map((a) => a.interleaved)).toEqual([false]);
    }
  });

  it("ignores system rows when deciding whether speech shares the clock", () => {
    const { activity } = buildCallActivity({
      tree: call([tool("lookup", 3000, 4000)]),
      rootSpanId: "root",
      transcript: [{ role: "system", content: "prompt" }, ...absolute],
    });
    expect(activity[0]).toMatchObject({
      interleaved: true,
      anchorTurnId: "call-turn-3",
    });
  });

  it("takes tools only from the chosen call's subtree", () => {
    const tree = [
      ...call([tool("mine", 3000, 4000)]),
      span("other-root", { observation_type: "conversation" }, [
        tool("theirs", 3000, 4000),
      ]),
      tool("orphan", 3000, 4000),
    ];
    const { activity } = buildCallActivity({
      tree,
      rootSpanId: "root",
      transcript: absolute,
    });
    expect(activity.map((a) => a.key)).toEqual(["mine"]);
    expect(
      buildCallActivity({ tree, rootSpanId: "missing", transcript: absolute })
        .activity,
    ).toEqual([]);
    expect(
      buildCallActivity({ tree, rootSpanId: null, transcript: absolute })
        .activity,
    ).toEqual([]);
  });

  it("shows a finish only from a recorded end at or after the start", () => {
    const { activity } = buildCallActivity({
      tree: call([
        tool("no-end", 1000, null, { latency_ms: 0 }),
        tool("backwards", 2000, 1500),
        tool("no-start", null, 4000, { start_time: "" }),
      ]),
      rootSpanId: "root",
      transcript: [],
    });
    expect(activity.map((a) => [a.key, a.startMs, a.endMs])).toEqual([
      ["no-end", 1000, null],
      ["backwards", 2000, null],
      ["no-start", null, null],
    ]);
  });

  it("names the tool, marks MCP and failure only with evidence, and copies no payload", () => {
    const { activity } = buildCallActivity({
      tree: call([
        tool("a", 1000, 1200, {
          name: "function_tool",
          span_attributes: {
            "gen_ai.tool.name": "lookup_order",
            "mcp.method.name": "tools/call",
          },
          status: "ERROR",
        }),
        tool("b", 2000, 2100, {
          span_attributes: { "lk.function_tool.is_error": true },
        }),
        tool("c", 3000, 3100, { span_attributes: { "tool.name": "refund" } }),
      ]),
      rootSpanId: "root",
      transcript: [],
    });
    expect(activity.map((a) => [a.name, a.mcp, a.failed])).toEqual([
      ["lookup_order", true, true],
      ["b", false, true],
      ["refund", false, false],
    ]);
    expect(JSON.stringify(activity)).not.toMatch(/canary/);
  });

  it("places tools only among speech: tool results and actor actions do not count", () => {
    const transcript = [
      absolute[0],
      absolute[1],
      // An untimed tool result and an actor action leave the speech clock intact.
      { role: "tool", content: "{}" },
      { role: "world_actor", content: "hangs up" },
      // A timed function result is never the turn a tool sits before.
      { role: "function", content: "{}", timestamp: at(3500) },
      absolute[2],
    ];
    const { activity } = buildCallActivity({
      tree: call([tool("lookup", 3000, 4000)]),
      rootSpanId: "root",
      transcript,
    });
    expect(activity[0]).toMatchObject({
      interleaved: true,
      anchorTurnId: "call-turn-5",
    });
  });

  it("groups the tools when the shown turn order disagrees with the wall clock", () => {
    // A states only its wall clock (+1 s). B also states a recording offset
    // (3 s), which the transcript sorts by, so B is shown before A.
    const transcript = [
      { role: "user", content: "A", timestamp: at(1000) },
      {
        role: "assistant",
        content: "B",
        timestamp: at(3000),
        start_time_seconds: 3,
      },
    ];
    const { activity } = buildCallActivity({
      tree: call([tool("early", 500, 600), tool("middle", 2000, 2100)]),
      rootSpanId: "root",
      transcript,
    });
    expect(activity.map((a) => [a.key, a.interleaved])).toEqual([
      ["early", false],
      ["middle", false],
    ]);
  });

  it("finds LiveKit's own function-tool span by the tool it names, untyped or typed", () => {
    const { activity } = buildCallActivity({
      tree: call([
        // Exported by LiveKit itself: nothing typed it.
        tool("native", 1000, 1200, {
          name: "function_tool",
          observation_type: "unknown",
          span_attributes: {
            "lk.function_tool.id": "call_1",
            "lk.function_tool.name": "lookup_order",
            "lk.function_tool.arguments": "canary-args",
          },
        }),
        // An untyped span without that evidence is not a tool.
        tool("plain", 1500, 1600, {
          name: "function_tool",
          observation_type: "unknown",
        }),
        // traceAI's mapping of the same span.
        tool("mapped", 2000, 2100, {
          name: "function_tool",
          span_attributes: { "lk.function_tool.name": "refund" },
        }),
      ]),
      rootSpanId: "root",
      transcript: [],
    });
    expect(activity.map((a) => [a.key, a.name])).toEqual([
      ["native", "lookup_order"],
      ["mapped", "refund"],
    ]);
    expect(JSON.stringify(activity)).not.toMatch(/canary/);
  });

  it("does not mutate its inputs, keeps existing ids and bounds the rows", () => {
    const transcript = [
      { id: 7, role: "user", content: "hi", timestamp: at(0) },
    ];
    const tree = call(
      Array.from({ length: MAX_ACTIVITY + 5 }, (_, i) =>
        tool(`t${String(i).padStart(4, "0")}`, 100 + i, 200 + i),
      ),
    );
    const before = JSON.stringify({ transcript, tree });
    const result = buildCallActivity({ tree, rootSpanId: "root", transcript });
    expect(JSON.stringify({ transcript, tree })).toBe(before);
    expect(result.transcript[0].id).toBe(7);
    expect(result.activity).toHaveLength(MAX_ACTIVITY);
  });
});
