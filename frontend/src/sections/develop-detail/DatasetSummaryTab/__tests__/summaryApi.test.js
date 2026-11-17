// Dataset Summary readers (L8, §3.14): snake_case in, one row per eval out.
import { describe, it, expect } from "vitest";
import {
  bestAgreementFor,
  camelizeKeys,
  failingText,
  flattenEvalStats,
  pct,
  toMiniHistogram,
} from "../summaryApi";
import { defaultPair } from "../JudgeHumanMatrix";

const stats = [
  {
    id: "T1",
    name: "groundedness_policy_kb",
    output_type: "score",
    result: [
      {
        id: "E2",
        name: "Groundedness",
        total_cells: 245,
        evaluator_version: "v3",
        latest_version: "v3",
        higher_is_better: true,
        pass_threshold: 0.7,
        pass_rate: 0.8449,
        mean: 0.79,
        below_threshold: 38,
        failing_filters: [{ column_id: "C", filter_config: {} }],
        histogram: [{ x0: 0, x1: 0.1, count: 2 }],
      },
    ],
  },
  {
    id: "T0",
    name: "correctness_vs_reference",
    output_type: "Pass/Fail",
    result: [
      {
        id: "E1",
        name: "Correctness",
        total_cells: 245,
        below_threshold: 47,
        pass_threshold: null,
        mean: 0.8,
      },
    ],
  },
];

describe("dataset Summary readers", () => {
  it("flattens eval-stats into overview rows", () => {
    const [g, c] = flattenEvalStats(stats);
    expect(g).toMatchObject({
      id: "E2",
      name: "Groundedness",
      version: "v3",
      passThreshold: 0.7,
      failing: 38,
      scored: 245,
    });
    expect(c.passFail).toBe(true);
    expect(c.mean).toBeNull();
    expect(failingText(g)).toBe("38 rows below 0.70");
    expect(failingText(c)).toBe("47 rows fail");
    expect(failingText({ ...g, higherIsBetter: false, failing: 1 })).toBe(
      "1 row above 0.70",
    );
  });

  it("formats missing values as a dash, never NaN", () => {
    expect(pct(null)).toBe("—");
    expect(pct(0.8449)).toBe("84.5%");
    expect(
      toMiniHistogram([{ x0: 0, x1: 0.1, count: 3 }], false).bins[0].counts
        .rows,
    ).toBe(3);
  });

  it("camelizes keys but leaves values", () => {
    expect(
      camelizeKeys({
        dataset_coverage: 41,
        labels: [{ label_name: "Correct?" }],
      }),
    ).toEqual({
      datasetCoverage: 41,
      labels: [{ labelName: "Correct?" }],
    });
  });

  it("picks the judge-vs-human pair: dataset eval order, then most rows", () => {
    const pairs = [
      { eval_id: "E2", label_id: "L1", n: 96, agreement_pct: 0.69 },
      { eval_id: "E1", label_id: "L1", n: 96, agreement_pct: 0.84 },
      { eval_id: "E1", label_id: "L2", n: 40, agreement_pct: 0.9 },
    ];
    expect(defaultPair(pairs, ["E1", "E2"]).label_id).toBe("L1");
    expect(bestAgreementFor(pairs, "E1").agreement_pct).toBe(0.84);
    expect(bestAgreementFor(pairs, "E9")).toBeNull();
  });
});

describe("R3 evals table helpers", () => {
  it("kappa bands and sentence", async () => {
    const { kappaBand, kappaText } = await import("../summaryApi");
    expect(kappaBand(0.62)).toEqual({ word: "substantial", tone: "good" });
    expect(kappaBand(0.17).tone).toBe("poor");
    expect(kappaText(0.62, 96)).toMatch(
      /^Cohen's kappa 0\.62 = substantial agreement on 96 paired rows\./,
    );
  });
  it("sorts lowest pass rate first; missing last", async () => {
    const { sortEvalRows } = await import("../summaryApi");
    const rows = [
      { id: "a", name: "Tone", passRate: 0.898 },
      { id: "b", name: "Correctness", passRate: 0.808 },
      { id: "c", name: "New", passRate: null },
    ];
    expect(sortEvalRows(rows, "passRate", "asc").map((r) => r.id)).toEqual([
      "b",
      "a",
      "c",
    ]);
    expect(sortEvalRows(rows, "name", "asc")[0].id).toBe("b");
  });
  it("pct/num use the one formatter", async () => {
    const { pct, num } = await import("../summaryApi");
    expect(pct(0.8082)).toBe("80.8%");
    expect(pct(null)).toBe("—");
    expect(num(0.6249)).toBe("0.62");
  });
});
