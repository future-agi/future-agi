import { describe, it, expect } from "vitest";
import {
  resolveEvalKind,
  EVAL_KIND,
  choiceTone,
  scoreTone,
  evalCellChips,
} from "../evalCellModel";

const score = { outputType: "score" };
const passfail = { outputType: "Pass/Fail" };
const choices = { outputType: "choices" };

describe("resolveEvalKind", () => {
  it("maps pass/fail spellings to PASS_FAIL", () => {
    expect(resolveEvalKind({ outputType: "pass/fail" })).toBe(EVAL_KIND.PASS_FAIL);
    expect(resolveEvalKind({ outputType: "pass_fail" })).toBe(EVAL_KIND.PASS_FAIL);
    expect(resolveEvalKind({ outputType: "boolean" })).toBe(EVAL_KIND.PASS_FAIL);
  });
  it("maps choice spellings to CHOICE", () => {
    expect(resolveEvalKind({ outputType: "choices" })).toBe(EVAL_KIND.CHOICE);
    expect(resolveEvalKind({ outputType: "choice" })).toBe(EVAL_KIND.CHOICE);
  });
  it("defaults to NUMERIC (incl. null/unknown)", () => {
    expect(resolveEvalKind({ outputType: "score" })).toBe(EVAL_KIND.NUMERIC);
    expect(resolveEvalKind({})).toBe(EVAL_KIND.NUMERIC);
    expect(resolveEvalKind(null)).toBe(EVAL_KIND.NUMERIC);
  });
});

describe("scoreTone — numeric color bands (dev parity)", () => {
  it("is red (fail) below 40 — including the boundary just under", () => {
    expect(scoreTone(0)).toBe("fail");
    expect(scoreTone(39.99)).toBe("fail");
  });
  it("is orange (neutral) in [40,60)", () => {
    expect(scoreTone(40)).toBe("neutral");
    expect(scoreTone(59.99)).toBe("neutral");
  });
  it("is green (pass) at/above 60", () => {
    expect(scoreTone(60)).toBe("pass");
    expect(scoreTone(100)).toBe("pass");
  });
});

describe("choiceTone", () => {
  it("falls back to neutral when the label has no mapping", () => {
    expect(choiceTone("anger", { choicesMap: {} })).toBe("neutral");
    expect(choiceTone("anger", undefined)).toBe("neutral");
  });
  it("uses the mapped tone when present", () => {
    expect(choiceTone("anger", { choicesMap: { anger: "fail" } })).toBe("fail");
  });
});

describe("evalCellChips", () => {
  // Boundary / null
  it("returns [] for null/empty value", () => {
    expect(evalCellChips(null, score)).toEqual([]);
    expect(evalCellChips("", score)).toEqual([]);
  });

  // Failure path first: errored eval
  it("renders an errored eval as a single Error chip", () => {
    expect(evalCellChips({ error: true }, score)).toEqual([
      { label: "Error", tone: "errored" },
    ]);
  });

  // Numeric — the color fix: below-passing must be red, not neutral "plain"
  it("colors numeric scores by value (red <40, green >=60)", () => {
    expect(evalCellChips(32, score)).toEqual([{ label: "32%", tone: "fail" }]);
    expect(evalCellChips(72, score)).toEqual([{ label: "72%", tone: "pass" }]);
  });
  it("rounds numeric scores to 2 decimals", () => {
    expect(evalCellChips(16.234, score)).toEqual([
      { label: "16.23%", tone: "fail" },
    ]);
  });

  // Pass/Fail counts
  it("renders pass/fail counts, fail first, omitting zero buckets", () => {
    expect(evalCellChips({ pass: 2, fail: 1 }, passfail)).toEqual([
      { label: "Fail 1", tone: "fail" },
      { label: "Pass 2", tone: "pass" },
    ]);
    expect(evalCellChips({ pass: 3, fail: 0 }, passfail)).toEqual([
      { label: "Pass 3", tone: "pass" },
    ]);
  });
  it("renders a scalar pass/fail by the >=50 threshold", () => {
    expect(evalCellChips(80, passfail)).toEqual([
      { label: "Pass", tone: "pass" },
    ]);
    expect(evalCellChips("fail", passfail)).toEqual([
      { label: "Fail", tone: "fail" },
    ]);
  });

  // Choices — zero-filled aggregate keeps only non-zero labels, sorted desc
  it("renders only non-zero choice labels, highest count first", () => {
    expect(
      evalCellChips({ anger: 1, joy: 0, sadness: 2 }, choices),
    ).toEqual([
      { label: "sadness 2", tone: "neutral" },
      { label: "anger 1", tone: "neutral" },
    ]);
  });
  it("renders a choice array as label chips", () => {
    expect(evalCellChips(["anger"], choices)).toEqual([
      { label: "anger", tone: "neutral" },
    ]);
  });
});
