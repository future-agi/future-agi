// integration C5 (AG-09): run-level numbers (headline Success / Cost / Time cells,
// the Latency, Total cost and Error rate chart points) flag `Low sample ·
// n=20` from the run's scored rows, like the eval cells do from the eval's n.
import { describe, expect, it } from "vitest";
import { lowSampleNote, runNotes, scoredRowsOf } from "../metricStripModel";

const run = (n, status = "Completed", extra = {}) => ({
  run_id: `r${n}`,
  status,
  progress: { done: n, total: n },
  metrics: {
    aggregates: {
      "eval:correctness": { n },
      "ops:latency": { n },
      "human:quality": { n: 3 },
    },
  },
  ...extra,
});

describe("scoredRowsOf", () => {
  it("is the most rows any metric was measured on (human labels aside)", () => {
    expect(scoredRowsOf(run(20))).toBe(20);
    expect(
      scoredRowsOf({ metrics: { aggregates: { "ops:cost": { n: 7 } } } }),
    ).toBe(7);
    expect(scoredRowsOf({ progress: { done: 12 } })).toBe(12);
    expect(scoredRowsOf({})).toBeNull();
  });
});

describe("low-sample flags without an eval definition", () => {
  it("a finished 20-row run flags its run-level points", () => {
    expect(lowSampleNote(run(20))).toBe("Low sample · n=20");
    expect(runNotes(run(20))).toContain("Low sample · n=20");
  });
  it("246 rows never flags", () => {
    expect(lowSampleNote(run(246))).toBeNull();
    expect(runNotes(run(246))).not.toContain("Low sample · n=246");
  });
  it("an eval keeps its own n", () => {
    const r = run(246);
    r.metrics.aggregates["eval:tone"] = { n: 20 };
    expect(runNotes(r, { key: "eval:tone" })).toContain("Low sample · n=20");
  });
  it("a running run says partial, not low sample", () => {
    const notes = runNotes(
      run(10, "Running", { progress: { done: 10, total: 238 } }),
    );
    expect(notes.some((t) => /partial/.test(t))).toBe(true);
    expect(notes.some((t) => /Low sample/.test(t))).toBe(false);
  });
});
