// HO results R7: run metrics as series in the leaderboard strip (pure).
import { describe, expect, it } from "vitest";
import {
  isRunMetricKey,
  runMetricAxis,
  runMetricSeries,
  runMetricSummary,
} from "../stripRunMetrics";

const strip = [
  { run_id: "a", label: "#1A" },
  { run_id: "b", label: "#1B" },
  { run_id: "c", label: "#1C" },
];
const data = {
  metrics: [
    { id: "m1", name: "ship_score", format: "number", higher_is_better: true },
    { id: "m2", name: "pass_share", format: "percent", higher_is_better: true },
    { id: "m3", name: "cost_k", format: "usd", higher_is_better: false },
  ],
  values: {
    a: { m1: { value: 0.61 }, m2: { value: 0.5 }, m3: { value: 0.004 } },
    b: { m1: { value: -0.2 }, m2: { value: 0.75 }, m3: { value: 0.002 } },
    c: { m2: { value: null } },
  },
};

describe("strip run metrics", () => {
  it("one series per metric; percent on the 0-100 axis, the rest raw", () => {
    const s = runMetricSeries(strip, data, ["red", "blue"]);
    expect(s.map((x) => x.key)).toEqual(["dm:m1", "dm:m2", "dm:m3"]);
    expect(s[0].data).toEqual([0.61, -0.2, null]);
    expect(s[1].data).toEqual([50, 75, null]);
    expect(s.map((x) => x.ownAxis)).toEqual([true, false, true]);
    expect(isRunMetricKey("dm:m1")).toBe(true);
    expect(isRunMetricKey("eval:tone")).toBe(false);
  });
  it("axes and ticks follow the metric's own format", () => {
    const [n, p, u] = runMetricSeries(strip, data, ["x"]);
    const an = runMetricAxis(n);
    expect(an.min).toBe(-0.2);
    expect(an.format(0.61)).toBe("0.61");
    expect(runMetricAxis(p).format(75)).toBe("75.0%");
    expect(runMetricAxis(u).note).toMatch(/↓ better/);
    expect(runMetricAxis({ data: [] })).toBeNull();
  });
  it("latest, best by direction and the baseline", () => {
    const sum = runMetricSummary(strip, data, data.metrics[2], strip[0]);
    expect(sum.latest.run.run_id).toBe("b");
    expect(sum.best.run.run_id).toBe("b");
    expect(sum.vs.value).toBe(0.004);
    expect(runMetricSummary(strip, { values: {} }, data.metrics[0])).toBeNull();
  });
});
