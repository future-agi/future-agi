// W5-1 A0/A1 · evaluator semantics over results rows (W5-SCOPE §5 A0, A1).
import { describe, expect, it } from "vitest";
import {
  compileExpression,
  evaluateExpression,
  matchesExpression,
  parseExpression,
} from "../expression";
import {
  expressionMatchesRow,
  expressionPillsError,
  resultsCatalogFor,
  resultsRowScope,
} from "../resultsScope";
import {
  gridExpressionMatchesRow,
  registerRowField,
  unregisterRowField,
} from "../gridScope";

const DEFS = {
  "eval:correctness": {
    key: "eval:correctness",
    name: "Correctness",
    output_type: "pass_fail",
    higher_is_better: true,
  },
  "eval:groundedness": {
    key: "eval:groundedness",
    name: "Groundedness",
    output_type: "score",
    pass_threshold: 0.7,
    higher_is_better: true,
  },
};
const COLUMNS = [
  { id: "c1", name: "customer_message", role: "input", data_type: "text" },
  { id: "c3", name: "expected_reply", role: "expected", data_type: "text" },
  { id: "c5", name: "intent", role: "metadata", data_type: "text" },
  { id: "c6", name: "language", role: "metadata", data_type: "text" },
];
const ctx = {
  runIds: ["A", "B", "C"],
  baselineId: "A",
  metricDefs: DEFS,
  runLabels: { A: "#2A", B: "#2B", C: "#2C" },
  splitNames: { s1: "smoke", s2: "hard" },
  columns: COLUMNS,
};
const run = (over) => ({
  status: "completed",
  output: "ok",
  values: {},
  movement: {},
  delta: {},
  human: {},
  trials: 1,
  flaky: false,
  latency_ms: 800,
  cost: 0.002,
  tokens: 300,
  steps: null,
  error: null,
  ...over,
});
const ROWS = [
  {
    example_id: "e1",
    row_order: 1,
    splits: ["s1"],
    metadata: {},
    values_latest: {
      c1: "Where is my REFUND for order 8830?",
      c5: "refund",
      c6: "es",
    },
    values_base: { c3: "Your refund was issued." },
    runs: {
      A: run({
        values: { "eval:correctness": 1, "eval:groundedness": 0.9 },
        output: "Refund issued",
        cost: 0.001,
      }),
      B: run({
        values: { "eval:correctness": 0, "eval:groundedness": 0.5 },
        movement: { "eval:correctness": "regressed" },
        delta: { "eval:correctness": -1 },
        output: '{"intent":"refund","order_id":"8830"}',
        cost: 0.004,
      }),
      C: run({
        values: { "eval:correctness": 0, "eval:groundedness": 0.71 },
        movement: { "eval:correctness": "regressed" },
        output: "",
      }),
    },
  },
  {
    example_id: "e2",
    row_order: 2,
    splits: [],
    metadata: { language: "de" },
    values_latest: { c1: "hello", c5: "billing" },
    values_base: { c3: "" },
    runs: {
      A: run({ values: { "eval:correctness": 0 }, output: "" }),
      B: run({
        status: "error",
        error: "RateLimitError",
        values: {},
        output: null,
      }),
      C: { status: "missing" },
    },
  },
  {
    example_id: "e3",
    row_order: 3,
    splits: ["s1", "s2"],
    metadata: {},
    values_latest: { c1: "Cancel please", c5: "cancel", c6: "en" },
    values_base: { c3: "Done." },
    runs: {
      A: run({ values: { "eval:correctness": 1 }, flaky: true, trials: 3 }),
      B: run({
        values: { "eval:correctness": 1 },
        movement: { "eval:correctness": "unchanged" },
        steps: { steps: 6, tool_calls: 3, tool_errors: 1 },
      }),
      C: run({ values: { "eval:correctness": 1 }, human: { lbl: "good" } }),
    },
  },
];

const ids = (text, c = ctx) => {
  const compiled = compileExpression(text, resultsCatalogFor(c));
  if (compiled.error) throw new Error(compiled.error.message);
  return ROWS.filter((r) =>
    matchesExpression(compiled.ast, resultsRowScope(r, c)),
  ).map((r) => r.example_id);
};

describe("operators × types", () => {
  it("compares numbers, text and lists", () => {
    expect(ids("#2B.cost_usd > 0.003")).toEqual(["e1"]);
    expect(ids("#2A.latency_ms >= 800 and #2A.tokens = 300")).toEqual([
      "e1",
      "e2",
      "e3",
    ]);
    expect(ids('customer_message = "hello"')).toEqual(["e2"]);
    expect(ids('customer_message = "HELLO"')).toEqual([]);
    expect(ids('customer_message contains "refund"')).toEqual(["e1"]);
    expect(ids('input.customer_message startswith "CANCEL"')).toEqual(["e3"]);
    expect(ids('customer_message not contains "e"')).toEqual([]);
    expect(ids('metadata.intent in ("refund", "cancel")')).toEqual([
      "e1",
      "e3",
    ]);
    expect(ids('metadata.intent not in ("refund")')).toEqual(["e2", "e3"]);
    expect(ids("len(expected) > 10")).toEqual(["e1"]);
    expect(ids("len(expected_reply) = 0")).toEqual(["e2"]);
  });

  it("reads metadata from the row, else the metadata-role columns", () => {
    expect(ids('metadata.language = "de"')).toEqual(["e2"]);
    expect(ids('metadata.language in ("es", "en")')).toEqual(["e1", "e3"]);
  });

  it("treats split and tags as lists", () => {
    expect(ids('split = "smoke"')).toEqual(["e1", "e3"]);
    expect(ids('split != "smoke"')).toEqual(["e2"]);
    expect(ids('split contains "HARD"')).toEqual(["e3"]);
    expect(ids('"hard" in split')).toEqual(["e3"]);
    expect(ids("split is empty")).toEqual(["e2"]);
    expect(ids('split in ("hard", "nope")')).toEqual(["e3"]);
  });

  it("arithmetic, functions and run-vs-run comparisons", () => {
    expect(ids("#2B.cost_usd - #2A.cost_usd > 0.002")).toEqual(["e1"]);
    expect(ids("#2B.groundedness < #2A.groundedness")).toEqual(["e1"]);
    expect(ids("round(#2B.cost_usd * 1000) = 4")).toEqual(["e1"]);
    expect(ids("abs(#2B.correctness.delta) = 1")).toEqual(["e1"]);
    expect(ids("max(#2A.cost_usd, #2B.cost_usd) = 0.004")).toEqual(["e1"]);
    expect(ids('coalesce(#2B.error, "none") = "RateLimitError"')).toEqual([
      "e2",
    ]);
    expect(ids('lower(#2A.output) = "refund issued"')).toEqual(["e1"]);
    expect(
      ids("#2B.steps = 6 and #2B.tool_calls = 3 and #2B.tool_errors > 0"),
    ).toEqual(["e3"]);
    expect(ids("#2A.cost_usd / 0 > 1")).toEqual([]);
  });

  it("reads JSON outputs by path", () => {
    expect(ids('#2B.output.intent = "refund"')).toEqual(["e1"]);
    expect(ids('#2B.output.order_id = "8830"')).toEqual(["e1"]);
    expect(ids("#2B.output.missing is empty")).toEqual(["e1", "e2", "e3"]);
  });
});

describe("missing values", () => {
  it("a comparison with a missing value is false, even !=", () => {
    expect(ids("#2C.groundedness < 1")).toEqual(["e1"]);
    expect(ids("#2C.groundedness != 0.71")).toEqual([]);
    expect(ids('#2B.output != "x"')).toEqual(["e1", "e3"]);
  });

  it("is empty: missing, '', [] and {}", () => {
    expect(ids("#2C.output is empty")).toEqual(["e1", "e2"]);
    expect(ids("#2A.output is not empty")).toEqual(["e1", "e3"]);
  });
});

describe("verdict words and eval parts", () => {
  it("a pass/fail eval compared with a verdict word compares its verdict", () => {
    expect(ids("#2B.correctness = fail")).toEqual(["e1"]);
    expect(ids("#2B.correctness = pass")).toEqual(["e3"]);
    expect(ids("#2B.correctness.verdict = error")).toEqual(["e2"]);
    expect(ids("#2B.correctness = error")).toEqual(["e2"]);
    expect(ids("#2C.groundedness = pass")).toEqual(["e1"]);
    expect(ids("#2B.status = error")).toEqual(["e2"]);
    expect(ids("#2C.status = missing")).toEqual(["e2"]);
    expect(ids("#2B.correctness.movement in (regressed, improved)")).toEqual([
      "e1",
    ]);
    expect(ids("#2B.correctness.movement = regressed")).toEqual(["e1"]);
    expect(ids("#2B.correctness < 0.5")).toEqual(["e1"]);
  });

  it("bare verdict words name a field in subject position", () => {
    expect(ids('#2B.error contains "rate"')).toEqual(["e2"]);
    expect(ids("error is not empty")).toEqual(["e2"]);
  });
});

describe("quantifiers", () => {
  it("unprefixed = any candidate when 2+ runs are in view", () => {
    expect(ids("correctness = fail")).toEqual(["e1"]);
    expect(ids("flaky")).toEqual([]);
    expect(ids("any.flaky")).toEqual(["e3"]);
  });

  it("any = at least one run, all = every run, baseline = the baseline", () => {
    expect(ids("any.correctness = fail")).toEqual(["e1", "e2"]);
    expect(ids("all.correctness = pass")).toEqual(["e3"]);
    expect(ids("not (all.output is empty)")).toEqual(["e1", "e3"]);
    expect(ids("baseline.correctness = fail")).toEqual(["e2"]);
    expect(ids("baseline.correctness = pass and correctness = fail")).toEqual([
      "e1",
    ]);
  });

  it("one run in view: unprefixed fields are that run", () => {
    const one = { ...ctx, runIds: ["B"], baselineId: "B" };
    expect(ids("correctness = fail", one)).toEqual(["e1"]);
  });

  it("refs by label, attempt label and id", () => {
    expect(ids('run("B").correctness = fail')).toEqual(["e1"]);
    expect(ids('run("#2b").correctness = fail')).toEqual(["e1"]);
  });
});

describe("pill helpers", () => {
  it("expressionMatchesRow parses once and evaluates", () => {
    const pill = { type: "expression", expr: "#2B.correctness = fail" };
    expect(
      ROWS.filter((r) => expressionMatchesRow(pill, r, ctx)).map(
        (r) => r.example_id,
      ),
    ).toEqual(["e1"]);
    expect(
      expressionMatchesRow(
        { type: "expression", expr: "lang = 1" },
        ROWS[0],
        ctx,
      ),
    ).toBe(false);
  });

  it("expressionPillsError gives the 400 text", () => {
    expect(
      expressionPillsError([{ type: "expression", expr: 'lang = "es"' }], ctx),
    ).toBe("Unknown field “lang”. Did you mean metadata.language?");
    expect(
      expressionPillsError(
        [{ type: "expression", expr: "#2Z.correctness = fail" }],
        ctx,
      ),
    ).toBe("Unknown run “#2Z”. Runs in view: #2A, #2B, #2C.");
    expect(
      expressionPillsError([{ type: "split", values: ["x"] }], ctx),
    ).toBeNull();
    expect(
      expressionPillsError(
        [{ type: "expression", expr: "#2B.correctness = fail" }],
        {
          runs: [{ id: "B", label: "#2B", metric_keys: ["eval:correctness"] }],
          metricOf: (k) => DEFS[k],
        },
      ),
    ).toBeNull();
  });

  it("evaluateExpression returns values for value expressions", () => {
    const scope = resultsRowScope(ROWS[0], ctx);
    expect(
      evaluateExpression(
        parseExpression("#2B.cost_usd - #2A.cost_usd").ast,
        scope,
      ),
    ).toBeCloseTo(0.003);
    expect(
      evaluateExpression(parseExpression("len(customer_message)").ast, scope),
    ).toBe(34);
  });
});

describe("row-field providers", () => {
  it("registered fields are offered and read in both scopes", () => {
    registerRowField("has_comment", {
      type: "boolean",
      read: ({ rowId }) => rowId === "e3" || rowId === "g2",
    });
    try {
      expect(ids("has_comment")).toEqual(["e3"]);
      const gctx = { columns: COLUMNS };
      const grid = [
        { id: "g1", n: 1, values: {} },
        { id: "g2", n: 2, values: {} },
      ];
      expect(
        grid
          .filter((r) =>
            gridExpressionMatchesRow("has_comment = true", r, gctx),
          )
          .map((r) => r.id),
      ).toEqual(["g2"]);
    } finally {
      unregisterRowField("has_comment");
    }
  });
});
