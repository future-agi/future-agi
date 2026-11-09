// options-truth L2 finalize: the header pill and the grid print one count.
import { afterEach, describe, expect, it } from "vitest";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { headerStatusText } from "../../matrix/headerVerdict";
import {
  countsByRun,
  countsKey,
  useLiveCounts,
  withSharedCounts,
} from "../liveCountShare";
import { withLiveProgress } from "../liveModel";

const run = (id, progress, extra = {}) => ({
  run_id: id,
  status: "Running",
  progress,
  ...extra,
});

describe("countsByRun", () => {
  it("keeps finite counts only, per run", () => {
    expect(
      countsByRun([
        run("a", { done: 2, total: 30, scored: 1, eta_s: 60, status: "x" }),
        run("b", { done: 5, total: 30, eta_s: null }),
        run("c", { total: 30 }),
        { run_id: "d" },
        null,
      ]),
    ).toEqual({
      a: { done: 2, total: 30, scored: 1, eta_s: 60 },
      b: { done: 5, total: 30 },
    });
  });
});

describe("withSharedCounts", () => {
  it("returns the header's runs untouched without grid counts", () => {
    const runs = [run("a", { done: 3, total: 30 })];
    expect(withSharedCounts(runs, null)).toBe(runs);
    expect(withSharedCounts(null, null)).toEqual([]);
  });

  it("takes the grid's counts; status, errors and other runs stay", () => {
    const runs = [
      run("a", { done: 3, total: 30, errors: 1, status: "Running" }),
      run("b", { done: 7, total: 30 }, { status: "Completed" }),
    ];
    const out = withSharedCounts(runs, { a: { done: 2, total: 30 } });
    expect(out[0]).toMatchObject({
      status: "Running",
      progress: { done: 2, total: 30, errors: 1, status: "Running" },
    });
    expect(out[1]).toBe(runs[1]);
    expect(getRunStatus(out[0]).active).toBe(true);
  });

  it("the pill prints the number the grid's live summary prints", () => {
    // The experiment poll landed a row later than the results poll.
    const header = [run("a", { done: 3, total: 30 })];
    const grid = withLiveProgress(
      [run("a", { done: 2, total: 30 })],
      [run("a", { done: 2, total: 30 })],
      30,
    );
    const runs = withSharedCounts(header, countsByRun(grid));
    const p = runs[0].progress;
    expect(
      headerStatusText(getRunStatus(runs[0]), { done: p.done, total: p.total }),
    ).toBe("Running · 2/30");
  });
});

describe("countsKey", () => {
  it("moves only when a number moves", () => {
    const a = countsKey({
      x: { done: 1, total: 30 },
      y: { done: 2, total: 9 },
    });
    const b = countsKey({
      y: { done: 2, total: 9 },
      x: { done: 1, total: 30 },
    });
    const c = countsKey({
      x: { done: 2, total: 30 },
      y: { done: 2, total: 9 },
    });
    expect(a).toBe(b);
    expect(a).not.toBe(c);
    expect(countsKey(null)).toBe("");
  });
});

describe("useLiveCounts", () => {
  afterEach(() => useLiveCounts.setState({ byExperiment: {} }));

  it("publishes per experiment and clears on leave", () => {
    const { publish, clear } = useLiveCounts.getState();
    publish("e1", { a: { done: 1, total: 4 } });
    publish("e2", { b: { done: 2, total: 4 } });
    expect(useLiveCounts.getState().byExperiment).toEqual({
      e1: { a: { done: 1, total: 4 } },
      e2: { b: { done: 2, total: 4 } },
    });
    clear("e1");
    expect(Object.keys(useLiveCounts.getState().byExperiment)).toEqual(["e2"]);
    const before = useLiveCounts.getState();
    clear("missing");
    expect(useLiveCounts.getState()).toBe(before);
  });
});
