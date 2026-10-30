import { describe, expect, it } from "vitest";

import {
  findAdjacentTraceRunIndex,
  getRunSpanId,
  getRunTraceId,
} from "../taskRunTrace";

const run = (traceId, spanId) => ({ trace_id: traceId, span_id: spanId });
const sessionRun = { trace_id: null, detail: { session_id: "sess-1" } };

describe("task run trace helpers", () => {
  it("reads the trace and span from the run, falling back to its detail", () => {
    expect(getRunTraceId(run("t1", "s1"))).toBe("t1");
    expect(getRunSpanId(run("t1", "s1"))).toBe("s1");
    expect(getRunTraceId({ detail: { trace_id: "t2" } })).toBe("t2");
    expect(getRunSpanId({ detail: { span_id: "s2" } })).toBe("s2");
  });

  it("has no trace for session-level or missing runs", () => {
    expect(getRunTraceId(sessionRun)).toBeNull();
    expect(getRunSpanId(sessionRun)).toBeNull();
    expect(getRunTraceId(undefined)).toBeNull();
  });

  it("steps over runs that score the same trace", () => {
    const runs = [run("A", "a1"), run("A", "a2"), run("B", "b1")];
    expect(findAdjacentTraceRunIndex(runs, 0, 1)).toBe(2);
    expect(findAdjacentTraceRunIndex(runs, 2, -1)).toBe(1);
  });

  it("skips runs without a trace", () => {
    const runs = [run("A", "a1"), sessionRun, run("B", "b1")];
    expect(findAdjacentTraceRunIndex(runs, 0, 1)).toBe(2);
    expect(findAdjacentTraceRunIndex(runs, 2, -1)).toBe(0);
  });

  it("returns -1 when no other trace lies in that direction", () => {
    const runs = [run("A", "a1"), run("A", "a2"), sessionRun];
    expect(findAdjacentTraceRunIndex(runs, 0, -1)).toBe(-1);
    expect(findAdjacentTraceRunIndex(runs, 0, 1)).toBe(-1);
    expect(findAdjacentTraceRunIndex([], 0, 1)).toBe(-1);
  });
});
