import { describe, expect, it } from "vitest";
import { durationEstimate } from "../costEstimateModel";

describe("durationEstimate (options-truth L1 C10)", () => {
  it("runs tries in waves of `concurrency`, one p50 per wave", () => {
    // 24 rows × 3 tries = 72 tries; 4 at a time = 18 waves × 12 s = 216 s.
    const d = durationEstimate({
      rows: 24,
      tries: 3,
      concurrency: 4,
      p50Ms: 12000,
    });
    expect(d.seconds).toBe(216);
    expect(d.minutes).toEqual([4, 5]);
  });
  it("more at a time is faster; more tries is slower", () => {
    const base = { rows: 30, tries: 1, concurrency: 2, p50Ms: 8000 };
    expect(durationEstimate({ ...base, concurrency: 10 }).seconds).toBeLessThan(
      durationEstimate(base).seconds,
    );
    expect(durationEstimate({ ...base, tries: 3 }).seconds).toBeGreaterThan(
      durationEstimate(base).seconds,
    );
  });
  it("never says 0 minutes and keeps lo ≤ hi", () => {
    const d = durationEstimate({
      rows: 1,
      tries: 1,
      concurrency: 8,
      p50Ms: 900,
    });
    expect(d.seconds).toBe(1);
    expect(d.minutes[0]).toBe(1);
    expect(d.minutes[1]).toBeGreaterThanOrEqual(d.minutes[0]);
  });
});
