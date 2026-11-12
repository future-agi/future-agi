// W5-1 A2 · autocomplete: the field before an operator is found by one lexer
// pass and a walk back over the tokens, so a long `in (…)` list followed by
// an operator answers at once (review fa50151 must-fix: the old regex
// backtracked ~2^items and froze the tab at about 20 items).
import { describe, expect, it } from "vitest";
import { suggest } from "../expressionSuggest";
import { resultsCatalog } from "../fieldCatalog";
import { EXPRESSION_LIMITS } from "../expressionLexer";

const CAT = resultsCatalog({
  runs: [
    { run_id: "r-a", label: "#2A" },
    { run_id: "r-b", label: "#2B" },
  ],
  baselineId: "r-a",
  metricDefs: [
    { key: "eval:correctness", name: "Correctness", output_type: "pass_fail" },
  ],
  metadataKeys: ["language"],
});
const HINTS = {
  values: {
    "metadata.language": [
      { value: "es", count: 2 },
      { value: "de", count: 1 },
    ],
  },
};
const list = (n) => Array.from({ length: n }, (_, i) => `"l${i}"`).join(", ");
const labelsAt = (text) =>
  suggest(text, text.length, CAT, HINTS).items.map((i) => i.label);
const timed = (text) => {
  const t0 = performance.now();
  const out = suggest(text, text.length, CAT, HINTS);
  return { ms: performance.now() - t0, labels: out.items.map((i) => i.label) };
};

describe("suggest · a long list followed by an operator", () => {
  it("answers in under 50 ms for a 200-item list then `and row > `", () => {
    const text = `metadata.language in (${list(200)}) and row > `;
    expect(text.length).toBeLessThanOrEqual(EXPRESSION_LIMITS.maxLength);
    const { ms } = timed(text);
    expect(ms).toBeLessThan(50);
  });

  it("still names the field after the list (values for an eval)", () => {
    const { ms, labels } = timed(
      `metadata.language in (${list(200)}) and #2B.correctness = `,
    );
    expect(ms).toBeLessThan(50);
    expect(labels).toEqual(["pass", "fail"]);
  });

  it("offers the list field's values inside a 200-item open list", () => {
    const { ms, labels } = timed(`metadata.language in (${list(200)}, `);
    expect(ms).toBeLessThan(50);
    expect(labels).toEqual(["es", "de"]);
  });

  it("answers at once on text past the length limit", () => {
    const { ms } = timed(`id in (${list(400)}) and row > `);
    expect(ms).toBeLessThan(50);
  });
});

describe("suggest · the field before the operator", () => {
  it.each([
    ["metadata.language = ", ["es", "de"]],
    ["metadata.language != ", ["es", "de"]],
    ["metadata.language in (", ["es", "de"]],
    ['metadata.language in ("es", ', ["es", "de"]],
    ['metadata.language not in ("es", ', ["es", "de"]],
    ["metadata.`language` = ", ["es", "de"]],
    ["(metadata.language = ", ["es", "de"]],
    // A comparison after another one, or after `not`: the field is the
    // operand right before the operator, never the words before it.
    ["row > 3 and metadata.language = ", ["es", "de"]],
    ["not metadata.language = ", ["es", "de"]],
    ["#2B.correctness = ", ["pass", "fail"]],
    ["baseline.correctness = ", ["pass", "fail"]],
    ["all.correctness = ", ["pass", "fail"]],
    ['run("#2B").correctness = ', ["pass", "fail"]],
    ["correctness = ", ["pass", "fail"]],
  ])("%s → %j", (text, want) => {
    expect(labelsAt(text)).toEqual(want);
  });

  it.each([
    "len(x) = ",
    "min(row, ",
    'metadata.language in ("es", len(x), ',
    'metadata.language = "es, ',
    "= ",
  ])("%s → no values", (text) => {
    expect(labelsAt(text)).toEqual([]);
  });
});
