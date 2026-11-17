import { describe, expect, it } from "vitest";
import {
  etaText,
  fixRunsNextToBaseline,
  liveProgress,
  newResultsSince,
} from "../liveModel";
import { showLabel } from "../ResultsToolbar";
import { sortLabel } from "../sortModel";

const run = (id, progress, extra = {}) => ({
  run_id: id,
  label: `#${id}`,
  progress,
  ...extra,
});

describe("live results (iterate r2 G3 P5/P7)", () => {
  it("sums the runs still scoring, from their own progress", () => {
    const runs = [
      run("B", { status: "Completed", scored: 30, total: 30 }),
      run("F", { status: "Running", scored: 12, total: 30, eta_s: 40 }),
    ];
    expect(liveProgress(runs)).toEqual({
      scored: 12,
      total: 30,
      eta: 40,
      label: "#F",
    });
    expect(liveProgress([runs[0]])).toBeNull();
    expect(etaText(40)).toBe("about 40 s left");
    expect(etaText(200)).toBe("about 3 min left");
    expect(etaText(2)).toBe("finishing");
  });
  it("counts the results that landed since the pause", () => {
    const then = [run("F", { status: "Running", scored: 12, total: 30 })];
    const now = [run("F", { status: "Running", scored: 21, total: 30 })];
    expect(newResultsSince(then, now)).toBe(9);
  });
  it("puts a fix-run (and its own fix-runs) next to the baseline, newest first", () => {
    const runs = [
      run("B", null),
      run("A", null),
      run("C", null),
      run("F", null, { parent_run_id: "B" }),
      run("G", null, { parent_run_id: "F" }),
    ];
    expect(fixRunsNextToBaseline(runs, "B").map((r) => r.run_id)).toEqual([
      "B",
      "G",
      "F",
      "A",
      "C",
    ]);
    expect(fixRunsNextToBaseline(runs.slice(0, 3), "B")).toEqual(
      runs.slice(0, 3),
    );
  });
});

describe("one regression word (P8)", () => {
  it("IT-R4 G3 P4: `Worse · Better · Changed` and `worse than baseline first`", () => {
    expect(showLabel("regressed", "#2B")).toBe("Worse");
    expect(showLabel("improved", "#2B")).toBe("Better");
    expect(sortLabel("regressed")).toBe("Sort: worse than baseline first");
  });
  it("has short forms for the one-row toolbar below 1536 px", () => {
    expect(showLabel("regressed", "#2B", { short: true })).toBe("Worse");
    expect(showLabel("changed", "#2B", { short: true })).toBe("Changed");
    expect(sortLabel("regressed", { short: true })).toBe("Sort: worse first");
    expect(sortLabel("order", { short: true })).toBe("Sort");
  });
});
