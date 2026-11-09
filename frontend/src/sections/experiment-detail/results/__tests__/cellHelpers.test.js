import { describe, expect, it } from "vitest";
import {
  filterPillForScore,
  opsTone,
  preferenceText,
} from "../cells/MetricCells";
import { diffSource } from "../cells/OutputCell";
import { extraFieldCount, linesFor, primaryText } from "../cells/cellCommon";
import { prefillVariablesFor } from "../drawer/OutputCard";
import {
  experimentsFromRuns,
  matchesExperiment,
  experimentStatusOf,
} from "../../DevelopExperimentSelect";
import { compareUrl } from "../../ExperimentRow";
import { compareBaseline } from "../../CompareWrapperView";
import { experimentBaseline } from "../../ExperimentData/ExperimentDataView";
import { versionsDifferText } from "../headers/ResultGroupHeader";

const passFail = {
  key: "eval:correctness",
  output_type: "pass_fail",
  higher_is_better: true,
};
const score = {
  key: "eval:groundedness",
  output_type: "score",
  higher_is_better: true,
  pass_threshold: 0.7,
};

describe("cell helpers", () => {
  it("Filter by this: movement first, then fails, then threshold", () => {
    expect(
      filterPillForScore(passFail, { value: 0 }, "regressed", "R"),
    ).toEqual({
      type: "movement",
      metric: "eval:correctness",
      dir: "regressed",
      run: "R",
    });
    expect(filterPillForScore(score, { value: 0.41 }, undefined, "R")).toEqual({
      type: "fails",
      metric: "eval:groundedness",
      run: "R",
    });
    expect(
      filterPillForScore(score, { value: 0.9 }, "unchanged", "R"),
    ).toMatchObject({
      type: "score",
      op: "gte",
      value: 0.9,
    });
  });

  it("ops tone is ±10 % relative", () => {
    expect(opsTone(1100, 1000)).toBe("bad");
    expect(opsTone(899, 1000)).toBe("good");
    expect(opsTone(1050, 1000)).toBe("neutral");
    expect(opsTone(5, null)).toBe("neutral");
  });

  it("preference text names the winning run", () => {
    const runsById = { A: { label: "#3A" }, B: { label: "#4A" } };
    expect(preferenceText({ winner: "B" }, "human", runsById)).toBe(
      "#4A better",
    );
    expect(preferenceText({ winner: "tie" }, "human", runsById)).toBe("Tie");
    expect(preferenceText({ judge: { winner: "A" } }, "judge", runsById)).toBe(
      "#3A better",
    );
    expect(preferenceText(null, "human", runsById)).toBe(null);
  });

  it("diff source: expected text or the baseline run's output", () => {
    const row = {
      expected: { primary: "expected_reply", value: { expected_reply: "ref" } },
      results: {
        A: { status: "completed", output: "base" },
        B: { status: "completed", output: "cand" },
      },
    };
    expect(diffSource(row, "B", { diff: "expected", baselineId: "A" })).toBe(
      "ref",
    );
    expect(diffSource(row, "B", { diff: "baseline", baselineId: "A" })).toBe(
      "base",
    );
    expect(diffSource(row, "A", { diff: "baseline", baselineId: "A" })).toBe(
      null,
    );
  });

  it("primary text, extra fields and lines", () => {
    expect(primaryText({ customer_message: "Hi", conversation: [] })).toBe(
      "Hi",
    );
    expect(extraFieldCount({ a: "x", b: [], c: "y" })).toBe(1);
    expect(linesFor(100)).toBe(4);
    expect(
      prefillVariablesFor({
        inputs: { customer_message: "Hi", conversation: [] },
        metadata: { customer_tier: "pro", n: 3 },
      }),
    ).toEqual({
      customer_message: "Hi",
      customer_tier: "pro",
    });
  });
});

describe("header and page helpers", () => {
  const runs = [
    {
      experiment_id: "E1",
      experiment_ordinal: 1,
      experiment_name: "baseline",
      status: "Completed",
    },
    {
      experiment_id: "E7",
      experiment_ordinal: 7,
      experiment_name: "temperature sweep",
      status: "Running",
    },
    {
      experiment_id: "E7",
      experiment_ordinal: 7,
      experiment_name: "temperature sweep",
      status: "Completed",
    },
  ];
  it("groups runs into experiments, newest first, and matches #n", () => {
    const exps = experimentsFromRuns(runs);
    expect(exps.map((e) => e.ordinal)).toEqual([7, 1]);
    expect(exps[0].runs).toHaveLength(2);
    expect(matchesExperiment(exps[0], "#7")).toBe(true);
    expect(matchesExperiment(exps[0], "#1")).toBe(false);
    expect(matchesExperiment(exps[0], "sweep")).toBe(true);
    expect(experimentStatusOf(exps[0].runs).key).toBe("Running");
  });

  it("compare links keep the picked runs first and the tab", () => {
    const url = compareUrl({
      datasetId: "DS",
      runIds: ["P", "X"],
      baselineId: "P",
      tab: "summary",
      from: "/dashboard/x?y=1",
    });
    expect(url.startsWith("/dashboard/develop/DS/compare/summary?")).toBe(true);
    const q = new URLSearchParams(url.split("?")[1]);
    expect(q.get("runs")).toBe("P,X");
    expect(q.get("baseline")).toBe("P");
    expect(q.get("from")).toBe("/dashboard/x?y=1");
  });

  it("baseline defaults (C18)", () => {
    expect(experimentBaseline(["A", "B"], "B")).toBe("B");
    expect(experimentBaseline(["A", "B"], "OTHER")).toBe("A");
    // R3 MUST 3: the dataset's pinned baseline wins over run A.
    expect(experimentBaseline(["A", "B"], null, "B")).toBe("B");
    expect(experimentBaseline(["A", "B"], "A", "B")).toBe("A");
    expect(experimentBaseline(["A", "B"], null, "Z")).toBe("A");
    expect(compareBaseline(["A", "P"], null, "P")).toBe("P");
    expect(compareBaseline(["A", "B"], "B", "P")).toBe("B");
    expect(compareBaseline(["A", "B"], "none", "P", { allowNone: true })).toBe(
      "none",
    );
    expect(compareBaseline(["A", "B"], "none", "P")).toBe("A");
  });

  it("eval versions differ text", () => {
    expect(
      versionsDifferText(
        { A: "v2", B: "v3", C: "v3" },
        { A: "#1A", B: "#9A", C: "#9B" },
      ),
    ).toBe("Eval versions differ: v2 (#1A) vs v3 (#9A, #9B)");
  });
});

describe("run picker key metric", () => {
  it("reads the first eval's pass rate or average from R1 metrics", async () => {
    const { keyMetricText } = await import(
      "src/sections/common/Runs/RunPickerPopover"
    );
    expect(
      keyMetricText({
        metric_keys: ["eval:correctness"],
        metrics: {
          aggregates: { "eval:correctness": { pass_rate: 0.821, n: 248 } },
        },
      }),
    ).toBe("Correctness 82.1%");
    expect(
      keyMetricText(
        {
          metric_keys: ["eval:groundedness"],
          metrics: {
            aggregates: {
              "eval:groundedness": { avg: 0.74, pass_rate: null, n: 5 },
            },
          },
        },
        [{ key: "eval:groundedness", name: "Groundedness" }],
      ),
    ).toBe("Groundedness 0.74");
    expect(keyMetricText({ metric_keys: ["eval:x"] })).toBe(null);
  });
});
