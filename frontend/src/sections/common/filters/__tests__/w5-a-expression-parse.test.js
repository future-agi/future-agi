// W5-1 A0 · parser, validator, printer and did-you-mean (W5-SCOPE §5 A0).
import { describe, expect, it } from "vitest";
import {
  EXPRESSION_LIMITS,
  ONE_QUANTIFIER,
  compileExpression,
  parseExpression,
  printExpression,
  validateExpression,
} from "../expression";
import { didYouMean, levenshtein } from "../didYouMean";
import { resultsCatalog } from "../fieldCatalog";

const RUNS = [
  { run_id: "r-a", label: "#2A" },
  { run_id: "r-b", label: "#2B" },
  { run_id: "r-c", label: "#2C" },
  { run_id: "r-b2", label: "#2B·2" },
];
const DEFS = [
  { key: "eval:correctness", name: "Correctness", output_type: "pass_fail" },
  {
    key: "eval:groundedness",
    name: "Groundedness",
    output_type: "score",
    pass_threshold: 0.7,
  },
  { key: "eval:tone", name: "Tone", output_type: "pass_fail" },
];
const CAT = resultsCatalog({
  runs: RUNS.slice(0, 3),
  baselineId: "r-a",
  metricDefs: DEFS,
  inputColumns: [{ id: "c1", name: "customer_message", data_type: "text" }],
  expectedColumns: [{ id: "c3", name: "expected_reply", data_type: "text" }],
  metadataKeys: ["intent", "language", "customer_tier"],
});
const CAT4 = resultsCatalog({
  ...{ runs: RUNS, baselineId: "r-a", metricDefs: DEFS },
});

const ast = (text) => {
  const r = parseExpression(text);
  if (r.error) throw new Error(r.error.message);
  return r.ast;
};
const perr = (text) => parseExpression(text).error;
const verr = (text, cat = CAT) => compileExpression(text, cat).error;
const roundTrip = (text) => printExpression(ast(text));

describe("precedence and every operator", () => {
  it("binds not > and > or, comparisons above logic, * above +", () => {
    const t = ast("a = 1 or b = 2 and not c = 3");
    expect(t.t).toBe("or");
    expect(t.items[1].t).toBe("and");
    expect(t.items[1].items[1].t).toBe("not");
    const s = ast("len(x) + 2 * 3 > 4");
    expect(s.t).toBe("cmp");
    expect(s.l.t).toBe("arith");
    expect(s.l.op).toBe("+");
    expect(s.l.r.op).toBe("*");
  });

  it.each([
    ["x = 1", "x = 1"],
    ["x != 'a'", 'x != "a"'],
    ["x == 1", "x = 1"],
    ["x > 1 AND y >= 2", "x > 1 and y >= 2"],
    ["x < 1 Or y <= 2", "x < 1 or y <= 2"],
    ["x in ('a', \"b\", 3, true, null)", 'x in ("a", "b", 3, true, null)'],
    ["x NOT IN (1, -2)", "x not in (1, -2)"],
    ["x contains 'Ref'", 'x contains "Ref"'],
    ["x not contains 'ref'", 'x not contains "ref"'],
    ["x startswith 'Hi'", 'x startswith "Hi"'],
    ["x is empty", "x is empty"],
    ["x IS NOT EMPTY", "x is not empty"],
    ["not (a = 1 or b = 2)", "not (a = 1 or b = 2)"],
    ["(a + b) * c > 0", "(a + b) * c > 0"],
    ["a - (b - c) > 0", "a - (b - c) > 0"],
    ["-x < -0.5", "-x < -0.5"],
    [
      "round(x, 2) = 0.33 and abs(y) > min(1, 2, z)",
      "round(x, 2) = 0.33 and abs(y) > min(1, 2, z)",
    ],
    ["coalesce(a, b, 'none') = 'none'", 'coalesce(a, b, "none") = "none"'],
    ["lower(x) = upper(y)", "lower(x) = upper(y)"],
    ["correctness = FAIL", "correctness = fail"],
    [
      "status in (error, skipped, na, pass)",
      "status in (error, skipped, na, pass)",
    ],
    [
      "`Draft reply (gpt-4o-mini)` contains 'sorry'",
      '`Draft reply (gpt-4o-mini)` contains "sorry"',
    ],
    ['tags contains "refund"', 'tags contains "refund"'],
    ['"refund" in tags', '"refund" in tags'],
  ])("%s prints as %s and re-parses to the same text", (text, printed) => {
    expect(roundTrip(text)).toBe(printed);
    expect(roundTrip(printed)).toBe(printed);
  });
});

describe("quoting and run refs", () => {
  it("reads both quote kinds with escapes", () => {
    expect(ast("x = 'it\\'s'").r.v).toBe("it's");
    expect(ast('x = "say \\"hi\\""').r.v).toBe('say "hi"');
  });

  it("reads every run-ref form", () => {
    expect(ast("#2B.correctness = fail").l.run).toEqual({
      kind: "label",
      label: "#2B",
    });
    expect(ast("#2b·2.cost_usd > 0").l.run).toEqual({
      kind: "label",
      label: "#2B·2",
    });
    expect(ast("baseline.output is empty").l.run).toEqual({ kind: "baseline" });
    expect(ast("ANY.status = error").l.run).toEqual({ kind: "any" });
    expect(ast("all.correctness = fail").l.run).toEqual({ kind: "all" });
    expect(ast('run("#2C").latency_ms > 900').l.run).toEqual({
      kind: "run",
      ref: "#2C",
    });
    expect(ast("#2B.output.order.items.0").path).toEqual([
      "output",
      "order",
      "items",
      "0",
    ]);
    expect(roundTrip("#2b·2.output.intent = 'refund'")).toBe(
      '#2B·2.output.intent = "refund"',
    );
    expect(roundTrip('run("r-c").output is empty')).toBe(
      'run("r-c").output is empty',
    );
  });

  it("resolves runs by label, attempt label, id, baseline, any and all", () => {
    expect(validateExpression(ast("#2b.correctness = fail"), CAT).ok).toBe(
      true,
    );
    expect(validateExpression(ast("#2B·2.tone = pass"), CAT4).ok).toBe(true);
    expect(
      validateExpression(ast('run("r-c").tone = pass'), CAT).refs[0],
    ).toMatchObject({ run: "r-c" });
    expect(
      validateExpression(ast("baseline.tone = pass"), CAT).refs[0],
    ).toMatchObject({ run: "r-a" });
    expect(
      validateExpression(ast("any.tone = pass and all.correctness = fail"), CAT)
        .ok,
    ).toBe(true);
  });
});

describe("error copy with offsets", () => {
  it("names the unknown field with did-you-mean", () => {
    const e = verr('lang = "es"');
    expect(e.message).toBe(
      "Unknown field “lang”. Did you mean metadata.language?",
    );
    expect([e.at, e.length]).toEqual([0, 4]);
    const e2 = verr("metadata.langauge = 'es'");
    expect(e2.message).toBe(
      "Unknown field “metadata.langauge”. Did you mean metadata.language?",
    );
    expect(verr("zzzz = 1").message).toBe("Unknown field “zzzz”.");
    expect(verr("metadata.intent = refundz").message).toBe(
      'Unknown field “refundz”. Text goes in quotes: "refundz".',
    );
  });

  it("names the unknown run and lists the runs in view", () => {
    const e = verr("#2Z.correctness = fail");
    expect(e.message).toBe("Unknown run “#2Z”. Runs in view: #2A, #2B, #2C.");
    expect([e.at, e.length]).toEqual([0, 3]);
  });

  it("explains < on a pass/fail eval with a verdict word", () => {
    const e = verr("Correctness < fail");
    expect(e.message).toBe(
      "“<” needs a number; Correctness is pass / fail. Try Correctness = fail.",
    );
    expect(e.at).toBe(12);
    expect(verr("#2B.correctness < 0.5")).toBeUndefined();
  });

  it("explains < on a text field with a number", () => {
    expect(verr("metadata.language > 3").message).toBe(
      "“>” needs a number; metadata.language is text.",
    );
  });

  it("reports a missing ), an empty in-list and a dangling and", () => {
    const e = perr("(a = 1 and b = 2");
    expect(e).toEqual({ message: "Missing “)”.", at: 0, length: 1 });
    expect(perr("x in ()").message).toBe("Expected a value after “in”.");
    expect(perr("x in").message).toBe("Expected a value after “in”.");
    const d = perr("a = 1 and");
    expect(d).toEqual({
      message: "Unexpected “and” at the end.",
      at: 6,
      length: 3,
    });
    expect(perr("a = 1 b").message).toBe(
      "Unexpected “b”. Join conditions with and / or.",
    );
    expect(perr("x = 'open").message).toMatch(/Missing closing quote/);
    expect(perr("foo(1) = 1").message).toBe(
      "Unknown function “foo”. Functions: len, lower, upper, abs, round, min, max, coalesce.",
    );
    expect(verr("len(a, b) > 1").message).toBe("len() takes 1 value.");
  });

  it("allows one any / all / unprefixed run field per comparison", () => {
    expect(verr("correctness = tone").message).toBe(ONE_QUANTIFIER);
    expect(verr("any.cost_usd > all.cost_usd").message).toBe(ONE_QUANTIFIER);
    expect(verr("#2B.cost_usd - #2A.cost_usd > 0.002")).toBeUndefined();
    expect(verr("correctness = fail and tone = fail")).toBeUndefined();
    // One run in view: unprefixed fields are that run.
    const one = resultsCatalog({ runs: RUNS.slice(0, 1), metricDefs: DEFS });
    expect(verr("correctness = tone", one)).toBeUndefined();
  });

  it("rejects a run before a row field", () => {
    expect(verr("#2B.metadata.language = 'es'").message).toBe(
      "“metadata.language” is a row field; remove the run before it.",
    );
  });

  it("enforces the length and depth limits", () => {
    const long = `x = "${"a".repeat(EXPRESSION_LIMITS.maxLength)}"`;
    expect(perr(long).message).toBe(
      "Expression is too long (2,000 characters at most).",
    );
    expect(perr(`${"(".repeat(40)}x = 1${")".repeat(40)}`).message).toBe(
      "Expression is nested too deeply (32 levels at most).",
    );
    expect(perr(`${"(".repeat(20)}x = 1${")".repeat(20)}`)).toBeUndefined();
  });
});

describe("did-you-mean", () => {
  it("ranks by distance, then catalogue order, at most 3", () => {
    expect(levenshtein("kitten", "sitting")).toBe(3);
    expect(
      didYouMean("tone.verdct", [
        "correctness.verdict",
        "tone.verdict",
        "groundedness.verdict",
        "x.verdict",
      ]),
    ).toEqual(["tone.verdict", "correctness.verdict", "groundedness.verdict"]);
    expect(didYouMean("lang", ["metadata.language", "latency_ms"])).toEqual([
      "metadata.language",
    ]);
    expect(didYouMean("cost", ["cost_usd", "metadata.customer_tier"])).toEqual([
      "cost_usd",
    ]);
    expect(didYouMean("qqqq", ["metadata.language"])).toEqual([]);
  });

  it("ties keep catalogue order", () => {
    expect(didYouMean("ab", ["ac", "ad", "ae", "af"])).toEqual([
      "ac",
      "ad",
      "ae",
    ]);
  });
});
