import { describe, expect, it } from "vitest";
import {
  activityCounts,
  collapseRunBursts,
  dayBucket,
  filterActivity,
  groupActivity,
} from "../activityModel";

const NOW = new Date(2026, 9, 16, 15, 0, 0).getTime();
const at = (daysAgo, h = 10) =>
  new Date(2026, 9, 16 - daysAgo, h, 0, 0).toISOString();
const run = (d, passed) => ({
  type: "run",
  at: at(d),
  passed,
  run_label: "#2A",
});

describe("activityModel (RV#22)", () => {
  it("buckets by local day", () => {
    expect(dayBucket(at(0), NOW)).toBe("Today");
    expect(dayBucket(at(1), NOW)).toBe("Yesterday");
    expect(dayBucket(at(4), NOW)).toBe("This week");
    expect(dayBucket(at(9), NOW)).toBe("Last week");
    expect(dayBucket(at(40), NOW)).toBe("Older");
    expect(dayBucket(null, NOW)).toBe("Older");
  });

  it("filters and counts by type", () => {
    const ev = [
      run(0, true),
      { type: "edit", at: at(0) },
      { type: "score", at: at(1) },
      { type: "queue", at: at(1) },
      { type: "splits", at: at(2) },
      { type: "created", at: at(30) },
    ];
    expect(activityCounts(ev)).toEqual({
      all: 6,
      runs: 1,
      edits: 2,
      annotations: 1,
      splits: 1,
    });
    expect(filterActivity(ev, "annotations").map((e) => e.type)).toEqual([
      "score",
    ]);
    expect(filterActivity(ev, "nope")).toHaveLength(6);
  });

  it("folds 3+ consecutive runs into one burst", () => {
    const out = collapseRunBursts([
      run(0, true),
      run(0, false),
      run(0, true),
      { type: "edit", at: at(0) },
      run(0, true),
    ]);
    expect(out.map((e) => e.type)).toEqual(["runs", "edit", "run"]);
    expect(out[0]).toMatchObject({ passed: 2, failed: 1 });
    expect(out[0].runs).toHaveLength(3);
  });

  it("groups by day, keeping order", () => {
    const g = groupActivity(
      [run(0, true), run(0, true), run(0, false), { type: "edit", at: at(9) }],
      NOW,
    );
    expect(g.map((x) => x.title)).toEqual(["Today", "Last week"]);
    expect(g[0].events[0].type).toBe("runs");
  });
});
