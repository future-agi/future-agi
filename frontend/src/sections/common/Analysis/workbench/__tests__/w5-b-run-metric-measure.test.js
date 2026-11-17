// W5 B2: run metrics as Explore measures (`rm:<id>`).
import { describe, expect, it } from "vitest";
import {
  isRunMetricKey,
  RUN_METRIC_PREFIX,
  runMetricMeasures,
} from "../runMetricModel";

describe("run metric measures", () => {
  it("lists each run metric as Run metrics ▸ <name>", () => {
    const opts = runMetricMeasures([
      { id: "m1", name: "ship_score" },
      { id: "m2", name: "cheap_quality" },
    ]);
    expect(opts.map((o) => o.key)).toEqual([
      `${RUN_METRIC_PREFIX}m1`,
      `${RUN_METRIC_PREFIX}m2`,
    ]);
    expect(opts[0].label).toBe("Run metrics ▸ ship_score");
    expect(opts[0].kind).toBe("run_metric");
    expect(runMetricMeasures(undefined)).toEqual([]);
  });
  it("tells a run metric key from the pivot's measures", () => {
    expect(isRunMetricKey("rm:m1")).toBe(true);
    expect(isRunMetricKey("eval:tone")).toBe(false);
    expect(isRunMetricKey(null)).toBe(false);
  });
});
