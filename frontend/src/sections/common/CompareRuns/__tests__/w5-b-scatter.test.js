// W5 B5: the two-run scatter's points, brush and text; delta bar rows.
import { describe, expect, it } from "vitest";
import { measureOptions } from "src/sections/common/Analysis/workbench/workbenchModel";
import { deltaBarRows } from "src/sections/common/Analysis/workbench/charts/deltaBarsModel";
import {
  brushSelect,
  extentOf,
  isDiscrete,
  jitterOf,
  scatterPoints,
  selectionText,
} from "../scatterModel";

const SCORE = {
  key: "eval:g",
  kind: "score",
  label: "G",
  higherIsBetter: true,
  def: { pass_threshold: 0.5 },
};
const matrix = {
  rows: [
    {
      row_id: "a",
      n: 1,
      cells: {
        b: { trials: [1], values: [0.9] },
        c: { trials: [1], values: [0.4] },
      },
    },
    {
      row_id: "b",
      n: 2,
      cells: {
        b: { trials: [1], values: [0.6] },
        c: { trials: [1], values: [0.8] },
      },
    },
    {
      row_id: "c",
      n: 3,
      cells: {
        b: { trials: [1, 0], values: [0.7, 0.5] },
        c: { trials: [0], values: [0.6] },
      },
    },
    {
      row_id: "d",
      n: 4,
      cells: {
        b: { trials: ["infra"], values: [null] },
        c: { trials: [1], values: [0.9] },
      },
    },
  ],
};

describe("scatterPoints", () => {
  it("one point per row both runs scored, per-row means", () => {
    const pts = scatterPoints(matrix, {
      baselineId: "b",
      candidateId: "c",
      measure: SCORE,
    });
    expect(pts.map((p) => p.id)).toEqual(["a", "b", "c"]);
    expect(pts[2].x).toBeCloseTo(0.6, 12);
    const pass = measureOptions([], { k: 1 })[0];
    const rp = scatterPoints(matrix, {
      baselineId: "b",
      candidateId: "c",
      measure: pass,
    });
    expect(rp.find((p) => p.id === "c")).toMatchObject({ x: 0.5, y: 0 });
  });
});

describe("brushSelect", () => {
  const pts = scatterPoints(matrix, {
    baselineId: "b",
    candidateId: "c",
    measure: SCORE,
  });
  it("selects by true values and splits by direction", () => {
    const sel = brushSelect(pts, { x0: 0.55, x1: 1, y0: 0, y1: 0.65 }, SCORE);
    expect(sel.ids.sort()).toEqual(["a", "c"]);
    expect(sel.worse).toEqual(["a"]);
    expect(sel.same).toEqual(["c"]);
    expect(sel.better).toEqual([]);
    expect(selectionText(sel)).toBe(
      "2 rows selected · 1 got worse · 0 got better · 1 unchanged",
    );
  });
  it("lower-is-better flips the direction", () => {
    const sel = brushSelect(
      pts,
      { x0: 0, x1: 1, y0: 0, y1: 1 },
      { higherIsBetter: false },
    );
    expect(sel.better).toEqual(["a"]);
    expect(sel.worse).toEqual(["b"]);
  });
});

describe("display helpers", () => {
  it("jitter is deterministic and bounded", () => {
    const a = jitterOf("row-1", 1, 0.03);
    expect(jitterOf("row-1", 1, 0.03)).toEqual(a);
    expect(Math.abs(a.dx)).toBeLessThanOrEqual(0.015);
    expect(jitterOf("row-1", 1, 0)).toEqual({ dx: 0, dy: 0 });
  });
  it("extent and discreteness", () => {
    expect(extentOf([], SCORE)).toEqual({ min: 0, max: 1 });
    expect(extentOf([{ x: 1, y: 1 }], { kind: "rate" })).toEqual({
      min: 0,
      max: 1,
    });
    expect(
      isDiscrete([
        { x: 0, y: 1 },
        { x: 1, y: 1 },
      ]),
    ).toBe(true);
  });
});

describe("deltaBarRows (R5 vs_baseline, unchanged numbers)", () => {
  const analysis = {
    metrics: [
      {
        key: "eval:c",
        name: "Correctness",
        family: "eval",
        output_type: "pass_fail",
        higher_is_better: true,
        vs_baseline: [
          { run_id: "x", delta: 0.071429, ci95: 0.040249, significant: true },
        ],
      },
      {
        key: "ops:cost",
        name: "Cost",
        family: "ops",
        higher_is_better: false,
        vs_baseline: [
          { run_id: "x", delta: 0.002227, ci95: 0.000033, significant: true },
        ],
      },
      {
        key: "human:h",
        name: "Human",
        family: "human",
        vs_baseline: [{ run_id: "x", delta: 1 }],
      },
    ],
  };
  it("keeps R5's delta, ci95 and significance; pts for pass rates", () => {
    const rows = deltaBarRows(analysis, "x");
    expect(rows.map((r) => r.name)).toEqual(["Correctness", "Cost"]);
    expect(rows[0]).toMatchObject({
      delta: 0.071429,
      ci95: 0.040249,
      significant: true,
      better: true,
    });
    expect(rows[0].text).toBe("+7.1 pts ± 4.0 pts · significant");
    expect(rows[1].better).toBe(false);
    expect(rows[1].text).toBe("+$0.002227 ± $0.000033 · significant");
  });
});
