// runStats on literal vectors: the R2-SPEC §8.2 table (TS #2, runs A–E,
// 3 trials) and the iteration vectors. runStats.world.test.js checks the
// same numbers over the built world and the R3 matrix wire.
import { describe, expect, it } from "vitest";
import * as runStats from "../runStats";
import {
  disagreement,
  estimateRun,
  gapSlices,
  pareto,
  passAt1,
  passHatK,
  pivot,
  rowPassRate,
  signTest,
  wilson,
} from "../runStats";

// §8.2: per row, runs A B C D E. 1 pass · 0 fail · I infra · X error.
const TABLE = `
T1 111 000 111 111 111
T2 111 000 111 111 111
T3 111 111 111 111 111
T4 000 111 111 111 101
T5 111 111 111 111 111
T6 0X0 111 011 111 101
T7 111 111 111 111 111
T8 000 111 111 111 111
T9 111 111 111 111 111
T10 000 000 101 110 100
T11 111 000 111 111 111
T12 000 111 010 111 110
T13 101 111 111 1I1 111
T14 101 111 111 111 110
T15 111 111 111 111 111
T16 111 111 111 111 111
T17 111 000 111 111 111
T18 000 101 100 101 111
T19 000 010 100 110 010
T20 000 000 111 110 111
T21 000 110 100 011 111
T22 111 111 111 111 111
T23 000 010 111 011 11X
T24 011 111 101 111 111
T25 100 111 111 101 0X0
T26 111 000 111 111 111
T27 000 111 101 111 101
T28 011 011 111 110 101
T29 111 000 111 111 111
T30 000 111 110 111 101`;
const LETTERS = ["A", "B", "C", "D", "E"];
const CATEGORY = {
  refund: [1, 2, 6, 19, 24, 26],
  order_status: [3, 8, 11, 14, 29],
  address_change: [4, 9, 13, 17],
  warranty_callback: [5, 12, 18, 27],
  multi_request: [10, 20, 23, 25, 28],
  escalation: [7, 15, 16, 21, 22, 30],
};
const MEDIUM = [1, 2, 6, 8, 11, 12, 14, 16, 17, 24, 26, 27, 30];
const toTrials = (v) =>
  [...v].map((c) =>
    c === "1" ? 1 : c === "0" ? 0 : c === "I" ? "infra" : "error",
  );
const PARSED = TABLE.trim()
  .split("\n")
  .map((line) => {
    const [label, ...vs] = line.trim().split(/\s+/);
    return { label, n: Number(label.slice(1)), vs };
  });
const categoryOf = (n) =>
  Object.keys(CATEGORY).find((k) => CATEGORY[k].includes(n));
/** Matrix of rows 1..maxRow with the given runs (letter → vector map). */
function matrixOf(maxRow = 30, extra = {}) {
  return {
    rows: PARSED.filter((r) => r.n <= maxRow).map((r) => ({
      row_id: r.label,
      n: r.n,
      title: r.label,
      group_values: {
        category: categoryOf(r.n),
        difficulty: MEDIUM.includes(r.n) ? "medium" : "other",
        source: r.n > 24 ? "session" : "file",
      },
      cells: Object.fromEntries([
        ...LETTERS.map((l, i) => [l, { trials: toTrials(r.vs[i]) }]),
        ...Object.entries(extra).map(([id, fn]) => [
          id,
          { trials: toTrials(fn(r)) },
        ]),
      ]),
    })),
  };
}
const runOf = (letter, maxRow = 30) =>
  PARSED.filter((r) => r.n <= maxRow).map((r) =>
    toTrials(r.vs[LETTERS.indexOf(letter)]),
  );
const PREFIX = [1, 2, 11, 17, 26, 29];
// #2F = B with the 6 prefix rows 111; #2G also T13 110, T23 011, T28 001.
const vec2F = (r) => (PREFIX.includes(r.n) ? "111" : r.vs[1]);
const vec2G = (r) =>
  PREFIX.includes(r.n)
    ? "111"
    : { 13: "110", 23: "011", 28: "001" }[r.n] ?? r.vs[1];

describe("runStats signatures", () => {
  it("exports every frozen name", () => {
    [
      "passAt1",
      "passHatK",
      "rowPassRate",
      "disagreement",
      "pareto",
      "signTest",
      "pivot",
      "gapSlices",
      "estimateRun",
      "wilson",
    ].forEach((name) => expect(typeof runStats[name]).toBe("function"));
  });
});

describe("passAt1", () => {
  it("TS #2 A 48/90 · B 59/90 · C 77/90 · E 75/90", () => {
    expect(passAt1(runOf("A"))).toMatchObject({ pass: 48, n: 90 });
    expect(passAt1(runOf("B"))).toMatchObject({ pass: 59, n: 90 });
    expect(passAt1(runOf("C"))).toMatchObject({ pass: 77, n: 90 });
    expect(passAt1(runOf("E"))).toMatchObject({ pass: 75, n: 90 });
    expect((passAt1(runOf("B")).rate * 100).toFixed(1)).toBe("65.6");
  });
  it('#2D excludes its one "infra" attempt: 81/89 = 91.0%', () => {
    const r = passAt1(runOf("D"));
    expect(r).toMatchObject({ pass: 81, n: 89 });
    expect((r.rate * 100).toFixed(1)).toBe("91.0");
  });
  it("#1 (T1–T24): A 39/72 · D 65/71 · E 63/72", () => {
    expect(passAt1(runOf("A", 24))).toMatchObject({ pass: 39, n: 72 });
    expect(passAt1(runOf("D", 24))).toMatchObject({ pass: 65, n: 71 });
    expect(passAt1(runOf("E", 24))).toMatchObject({ pass: 63, n: 72 });
  });
  it("iterations #2F 77/90 = 85.6% · #2G 76/90 = 84.4%", () => {
    expect(passAt1(PARSED.map((r) => toTrials(vec2F(r))))).toMatchObject({
      pass: 77,
      n: 90,
    });
    const g = passAt1(PARSED.map((r) => toTrials(vec2G(r))));
    expect(g).toMatchObject({ pass: 76, n: 90 });
    expect((g.rate * 100).toFixed(1)).toBe("84.4");
  });
  it('"error" counts as a fail; an all-infra run gives rate null', () => {
    expect(passAt1([[1, "error", 0]])).toEqual({ pass: 1, n: 3, rate: 1 / 3 });
    expect(passAt1([["infra", "infra"]])).toEqual({
      pass: 0,
      n: 0,
      rate: null,
    });
    expect(passAt1([])).toEqual({ pass: 0, n: 0, rate: null });
  });
  it("accepts R3 matrix cells", () => {
    expect(passAt1([{ trials: [1, 1, 0] }])).toMatchObject({ pass: 2, n: 3 });
  });
});

describe("passHatK", () => {
  it("k=3 on #2: A 13/30 · B 17/30 · C 21/30 · D 21/29 · E 19/30", () => {
    expect(passHatK(runOf("A"), 3)).toMatchObject({ pass: 13, n: 30 });
    expect(passHatK(runOf("B"), 3)).toMatchObject({ pass: 17, n: 30 });
    expect(passHatK(runOf("C"), 3)).toMatchObject({ pass: 21, n: 30 });
    expect(passHatK(runOf("D"), 3)).toMatchObject({ pass: 21, n: 29 });
    expect(passHatK(runOf("E"), 3)).toMatchObject({ pass: 19, n: 30 });
  });
  it("#1: D 17/23 (the infra row leaves n); #2F 23/30; #2G 22/30", () => {
    expect(passHatK(runOf("D", 24), 3)).toMatchObject({ pass: 17, n: 23 });
    expect(
      passHatK(
        PARSED.map((r) => toTrials(vec2F(r))),
        3,
      ),
    ).toMatchObject({ pass: 23, n: 30 });
    expect(
      passHatK(
        PARSED.map((r) => toTrials(vec2G(r))),
        3,
      ),
    ).toMatchObject({ pass: 22, n: 30 });
  });
  it("rows with fewer than k scored trials are left out of n", () => {
    expect(
      passHatK(
        [
          [1, 1],
          [1, 1, 1],
          [null, 1, 1],
        ],
        3,
      ),
    ).toEqual({
      pass: 1,
      n: 1,
      rate: 1,
    });
  });
});

describe("rowPassRate", () => {
  it('[1,0,1] → 2/3; ["infra","infra"] → null; error is a fail', () => {
    expect(rowPassRate([1, 0, 1])).toBeCloseTo(2 / 3, 9);
    expect(rowPassRate(["infra", "infra"])).toBeNull();
    expect(rowPassRate([1, "infra", 1])).toBe(1);
    expect(rowPassRate(["error", 1])).toBe(0.5);
  });
});

describe("disagreement", () => {
  const d = disagreement(matrixOf(30));
  it("#2: Everyone 7 · Nobody 2 · Disagree 17 · Flaky 16 (sets overlap)", () => {
    expect(d.all).toHaveLength(30);
    expect(d.everyone).toEqual(["T3", "T5", "T7", "T9", "T15", "T16", "T22"]);
    expect(d.nobody).toEqual(["T10", "T19"]);
    expect(d.disagree).toHaveLength(17);
    expect(d.flaky).toHaveLength(16);
  });
  it("sd is rounded to 9 decimals; T4, T12, T25 tie and sort by row number", () => {
    expect(d.sd.T4).toBe(d.sd.T12);
    expect(d.sd.T12).toBe(d.sd.T25);
    expect(d.sd.T4).toBeCloseTo(0.3887, 4);
    expect(d.sd.T3).toBe(0);
    const at = (id) => d.order.indexOf(id);
    expect(at("T4")).toBeLessThan(at("T12"));
    expect(at("T12")).toBeLessThan(at("T25"));
    expect(at("T25")).toBe(at("T4") + 2);
    expect(d.order.at(-1)).toBe("T22"); // last zero-sd row by number
  });
  it("a 1I1 run is not 'everyone'; runs a row lacks are skipped", () => {
    const m = {
      rows: [
        { row_id: "x", n: 1, cells: { A: [1, "infra", 1], B: [1, 1, 1] } },
        { row_id: "y", n: 2, cells: { A: [1, 1, 1] } },
      ],
    };
    const r = disagreement(m);
    expect(r.everyone).toEqual(["y"]);
    expect(r.nobody).toEqual([]);
  });
});

describe("pareto", () => {
  // Mean cost per attempt (§8.2) vs pass@1 on #2.
  const COST = { A: 0.004, B: 0.011, C: 0.019, D: 0.21, E: 0.12 };
  const points = LETTERS.map((l) => ({
    id: l,
    cost: COST[l],
    quality: passAt1(runOf(l)).rate,
  }));
  it("#2: E is dominated by C; A B C D on the frontier", () => {
    const res = Object.fromEntries(pareto(points).map((p) => [p.id, p]));
    expect(res.E).toEqual({ id: "E", frontier: false, dominated_by: "C" });
    ["A", "B", "C", "D"].forEach((id) =>
      expect(res[id]).toEqual({ id, frontier: true, dominated_by: null }),
    );
  });
  it("#1: all five on the frontier", () => {
    const res = pareto(
      LETTERS.map((l) => ({
        id: l,
        cost: COST[l],
        quality: passAt1(runOf(l, 24)).rate,
      })),
    );
    expect(res.every((p) => p.frontier)).toBe(true);
  });
  it("with #2G at B's cost: frontier A → #2G → C → D; B and E dominated", () => {
    const g = {
      id: "2G",
      cost: 0.011,
      quality: passAt1(PARSED.map((r) => toTrials(vec2G(r)))).rate,
    };
    const res = Object.fromEntries(
      pareto([...points, g]).map((p) => [p.id, p]),
    );
    expect(
      Object.values(res)
        .filter((p) => p.frontier)
        .map((p) => p.id)
        .sort(),
    ).toEqual(["2G", "A", "C", "D"]);
    expect(res.B.dominated_by).toBe("2G");
    expect(res.E.dominated_by).toBe("C");
  });
  it("points without numbers are neither on nor off the frontier", () => {
    expect(pareto([{ id: "x", cost: null, quality: 0.5 }])).toEqual([
      { id: "x", frontier: false, dominated_by: null },
    ]);
  });
});

describe("signTest", () => {
  it("6/0 → p 0.031 significant; 7/2 → p 0.18 within noise", () => {
    const a = signTest(6, 0);
    expect(a.p).toBeCloseTo(0.03125, 6);
    expect(Number(a.p.toPrecision(2))).toBe(0.031);
    expect(a.significant).toBe(true);
    const b = signTest(7, 2);
    expect(Number(b.p.toPrecision(2))).toBe(0.18);
    expect(b.significant).toBe(false);
  });
  it("(9, 1) → p ≈ 0.0215, significant; (6, 4) → p ≈ 0.754, not; 0/0 → 1", () => {
    expect(signTest(9, 1).p).toBeCloseTo(0.0215, 4);
    expect(signTest(9, 1).significant).toBe(true);
    expect(signTest(6, 4).p).toBeCloseTo(0.754, 3);
    expect(signTest(6, 4).significant).toBe(false);
    expect(signTest(0, 0)).toEqual({ p: 1, significant: false });
  });
});

describe("pivot", () => {
  const m = matrixOf(30);
  const pct = (v) => Math.round(v * 100);
  it("rows=category × cols=run, pass@1: §6.3.2 cells", () => {
    const p = pivot(m, { rows: "category", cols: "run", measure: "pass_at_1" });
    expect(p.colKeys).toEqual(LETTERS);
    const row = (cat) => LETTERS.map((l) => pct(p.cells[cat][l].value));
    expect(row("refund")).toEqual([61, 39, 78, 94, 83]);
    expect(row("order_status")).toEqual([73, 60, 100, 100, 93]);
    expect(row("address_change")).toEqual([67, 75, 100, 100, 92]);
    expect(row("warranty_callback")).toEqual([25, 92, 58, 92, 83]);
    expect(row("multi_request")).toEqual([20, 40, 93, 67, 53]);
    expect(row("escalation")).toEqual([67, 94, 83, 94, 94]);
    expect(p.cells.address_change.D).toMatchObject({
      pass: 11,
      n: 11,
      rows: 4,
    });
  });
  it("totals.all equals passAt1 of the runs in view", () => {
    const p = pivot(m, { rows: "category", cols: "run", measure: "pass_at_1" });
    const all = passAt1(LETTERS.flatMap((l) => runOf(l)));
    expect(p.totals.all).toBeCloseTo(all.rate, 12);
    expect(p.totals.cols.B).toBeCloseTo(59 / 90, 12);
  });
  it("pass^k counts rows; config columns via runInfo; numeric measures", () => {
    const p = pivot(m, { rows: "none", cols: "run", measure: "pass_hat_k" });
    expect(p.cells["All rows"].D).toMatchObject({ pass: 21, n: 29 });
    const byKind = pivot(m, {
      rows: "none",
      cols: "kind",
      measure: "pass_at_1",
      runInfo: { A: { kind: "prompt" }, B: { kind: "agent" } },
      runIds: ["A", "B"],
    });
    expect(byKind.colKeys).toEqual(["prompt", "agent"]);
    const cost = pivot(
      {
        rows: [
          {
            row_id: "r",
            cells: { A: { trials: [1, 0], cost_usd: [0.1, 0.3] } },
          },
        ],
      },
      { rows: "none", cols: "run", measure: "cost", agg: "mean" },
    );
    expect(cost.cells["All rows"].A.value).toBeCloseTo(0.2, 12);
  });
  it("an empty matrix gives empty keys and a null total", () => {
    expect(pivot({ rows: [] }, { rows: "category" })).toMatchObject({
      rowKeys: [],
      totals: { all: null },
    });
  });
});

describe("gapSlices", () => {
  it("#2D vs #2B: refund +55.6 · order_status +40.0 · difficulty medium +38.5", () => {
    const g = gapSlices(matrixOf(30), "B", "D", {
      keys: ["category", "difficulty", "source"],
    });
    expect(
      g.map((s) => [s.key, s.value, s.delta_pts, s.from, s.to, s.rows]),
    ).toEqual([
      ["category", "refund", 55.6, { pass: 7, n: 18 }, { pass: 17, n: 18 }, 6],
      [
        "category",
        "order_status",
        40,
        { pass: 9, n: 15 },
        { pass: 15, n: 15 },
        5,
      ],
      [
        "difficulty",
        "medium",
        38.5,
        { pass: 24, n: 39 },
        { pass: 39, n: 39 },
        13,
      ],
    ]);
    expect(g[0].row_ids).toEqual(["T1", "T2", "T6", "T19", "T24", "T26"]);
  });
  it("skips slices under 3 rows or over half the rows", () => {
    const g = gapSlices(matrixOf(30), "B", "D", { keys: ["difficulty"] });
    expect(g.map((s) => s.value)).toEqual(["medium"]);
  });
});

describe("estimateRun", () => {
  it("#2F: 6 rows × 3 trials = 18 attempts · ≈ $0.20 · about 10 s", () => {
    const e = estimateRun({
      rows: 6,
      trials: 3,
      targets: [{ key: "B" }],
      evals: [{ type: "check" }, { type: "judge" }],
      history: [{ target_key: "B", cost_per_attempt: 0.011, p50_ms: 9800 }],
    });
    expect(e.calls).toBe(18);
    expect(e.judge_calls).toBe(18);
    expect(e.cost_usd.toFixed(2)).toBe("0.20");
    expect(e.seconds).toBe(10);
    expect(e.rough).toBe(false);
  });
  it("#2G guard: Run 24 rows · ≈ $0.79 · about 15 s", () => {
    const e = estimateRun({
      rows: 24,
      trials: 3,
      targets: [{ key: "B", cost_per_attempt: 0.011 }],
      evals: [],
    });
    expect(e.calls).toBe(72);
    expect(e.cost_usd.toFixed(2)).toBe("0.79");
    expect(e.seconds).toBe(15);
  });
  it("TS carry-over: 6 new rows × 5 runs × 3 trials = 90 attempts · ≈ $6.21 (code bills nothing)", () => {
    const COST = { A: 0.004, B: 0.011, C: 0.019, D: 0.21, E: 0.12 };
    const e = estimateRun({
      rows: 6,
      trials: 3,
      targets: LETTERS.map((l) => ({
        key: l,
        kind: l === "C" ? "code" : "prompt",
      })),
      evals: [],
      history: LETTERS.map((l) => ({
        target_key: l,
        cost_per_attempt: COST[l],
      })),
    });
    expect(e.calls).toBe(90);
    expect(e.cost_usd.toFixed(2)).toBe("6.21");
  });
  it("no history → list price × median tokens, rough", () => {
    const e = estimateRun({
      rows: 10,
      trials: 1,
      targets: [{ key: "x", model: "gpt-4o-mini" }],
      evals: [],
    });
    expect(e.rough).toBe(true);
    expect(e.cost_usd).toBeGreaterThan(0);
    expect(Number.isFinite(e.seconds)).toBe(true);
  });
});

describe("wilson", () => {
  it("(0.5, 100) → {low ≈ 0.404, high ≈ 0.596}; n = 0 → {low: 0, high: 1}", () => {
    const w = wilson(0.5, 100);
    expect(w.low).toBeCloseTo(0.404, 3);
    expect(w.high).toBeCloseTo(0.596, 3);
    expect(wilson(0.3, 0)).toEqual({ low: 0, high: 1 });
    const one = wilson(1, 10);
    expect(one.high).toBe(1);
    expect(one.low).toBeGreaterThan(0.6);
  });
});
