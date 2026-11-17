// W5 B2: run-metric formulas in the one expression language.
import { describe, expect, it } from "vitest";
import {
  compileFormula,
  definitionErrors,
  evaluateFormula,
  fmtMetric,
  formulaCatalog,
  partsLine,
  rankRuns,
} from "../formulaModel";

const DEFS = [
  {
    key: "eval:correctness",
    name: "Correctness",
    kind: "eval",
    output_type: "pass_fail",
  },
  { key: "eval:tone", name: "Tone", kind: "eval", output_type: "pass_fail" },
  {
    key: "eval:tool_call_accuracy",
    name: "Tool-call accuracy",
    kind: "eval",
    output_type: "score",
  },
  { key: "ops:cost", name: "Cost", kind: "system", output_type: "usd" },
];
const AGG = {
  metrics: {
    "eval:correctness": { value: 0.82, n: 238 },
    "eval:tone": { value: 0.45, n: 236 },
  },
  cost_usd: 0.0031,
  latency_p50: 1960,
  latency_p90: 2400,
  tokens: 331,
  rows: 238,
  errors: 2,
  error_rate: 2 / 238,
};

describe("formula catalogue and compile", () => {
  it("names evals by field name, with .n, plus the run fields", () => {
    const names = formulaCatalog(DEFS).fields.map((f) => f.name);
    expect(names).toEqual(
      expect.arrayContaining([
        "correctness",
        "correctness.n",
        "tone",
        "tool_call_accuracy",
        "cost_usd",
        "latency_p90",
        "error_rate",
        "rows",
      ]),
    );
    expect(names).not.toContain("cost");
  });
  it("evaluates arithmetic and min/max/abs/round with parts", () => {
    const c = compileFormula(
      "0.7 * correctness + 0.3 * tone - 10 * cost_usd",
      DEFS,
    );
    const r = evaluateFormula(c, AGG);
    expect(r.value).toBeCloseTo(0.7 * 0.82 + 0.3 * 0.45 - 10 * 0.0031, 12);
    expect(r.parts).toEqual({
      cost_usd: 0.0031,
      correctness: 0.82,
      tone: 0.45,
    });
    expect(
      evaluateFormula(
        compileFormula("round(max(correctness, tone) * 100)", DEFS),
        AGG,
      ).value,
    ).toBe(82);
    expect(
      evaluateFormula(compileFormula("abs(tone - correctness)", DEFS), AGG)
        .value,
    ).toBeCloseTo(0.37, 12);
    expect(
      evaluateFormula(compileFormula("correctness.n - errors", DEFS), AGG)
        .value,
    ).toBe(236);
  });
  it("a missing aggregate gives no value, not a made-up one", () => {
    const r = evaluateFormula(
      compileFormula("tool_call_accuracy * 2", DEFS),
      AGG,
    );
    expect(r.value).toBeNull();
  });
  it("an unknown name errors with a hint; text functions are refused", () => {
    expect(compileFormula("0.5 * tonee", DEFS).error.message).toMatch(
      /Unknown field “tonee”.*tone/,
    );
    expect(compileFormula("len(tone)", DEFS).error.message).toMatch(
      /min, max, abs, round only/,
    );
    expect(compileFormula("", DEFS).error.message).toBe("Write a formula.");
  });
});

describe("definitions, formats, the parts line", () => {
  it("validates names (pattern, duplicates, field clash) and the format", () => {
    expect(
      definitionErrors(
        { name: "ship_score", formula: "tone", format: "number" },
        DEFS,
      ),
    ).toEqual({});
    expect(
      definitionErrors(
        { name: "Ship Score", formula: "tone", format: "number" },
        DEFS,
      ).name,
    ).toMatch(/lowercase/);
    expect(
      definitionErrors(
        { name: "tone", formula: "tone", format: "number" },
        DEFS,
      ).name,
    ).toMatch(/already a field/);
    expect(
      definitionErrors({ name: "a", formula: "tone", format: "number" }, DEFS, [
        { name: "a" },
      ]).name,
    ).toMatch(/already exists/);
    expect(
      definitionErrors({ name: "a", formula: "tone", format: "bogus" }, DEFS)
        .format,
    ).toBeTruthy();
  });
  it("formats and prints the parts line", () => {
    expect(fmtMetric(0.712, "percent")).toBe("71.2%");
    expect(fmtMetric(0.0031, "usd")).toBe("$0.0031");
    expect(fmtMetric(1960.4, "ms")).toBe("1,960 ms");
    expect(fmtMetric(-0.5, "number")).toBe("−0.5");
    const f = "0.7 * correctness + 0.3 * tone - 10 * cost_usd";
    const r = evaluateFormula(compileFormula(f, DEFS), AGG);
    expect(partsLine(f, r, "number")).toBe(
      "0.678 = 0.7 × 0.82 + 0.3 × 0.45 − 10 × 0.0031",
    );
  });
  it("ranks runs by the metric's direction", () => {
    const values = {
      a: { m: { value: 1 } },
      b: { m: { value: 3 } },
      c: { m: { value: 2 } },
    };
    expect(rankRuns(values, { id: "m", higher_is_better: true })).toEqual({
      best: "b",
      worst: "a",
    });
    expect(rankRuns(values, { id: "m", higher_is_better: false })).toEqual({
      best: "a",
      worst: "b",
    });
  });
});
