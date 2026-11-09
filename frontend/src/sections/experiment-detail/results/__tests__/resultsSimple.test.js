import { describe, expect, it } from "vitest";
import {
  expectedChecklist,
  expectedSentence,
  joinClauses,
  primaryPassMetric,
  trialOutcome,
  whyChips,
} from "../resultsSimple";

const EXPECTED = {
  value: {
    checks: [
      {
        key: "order_found",
        name: "Order found",
        description: "lookup_order returned A-8830",
      },
      {
        key: "refund_once",
        name: "Refund issued once",
        description: "exactly one refund on A-8830",
      },
      {
        key: "refund_amount",
        name: "Refund amount",
        description: "refund of $149",
      },
      {
        key: "timeline_stated",
        name: "Timeline stated",
        description: "gives a timeline",
      },
    ],
  },
};

describe("expectedSentence", () => {
  it("joins check descriptions into one sentence", () => {
    expect(joinClauses(["refund $129", "give a timeline"])).toBe(
      "refund $129 and give a timeline",
    );
    expect(expectedSentence(EXPECTED, { prefix: true })).toBe(
      "Expected: lookup_order returned A-8830, exactly one refund on A-8830, refund of $149 and 1 more check",
    );
  });
  it("falls back to the primary expected text", () => {
    expect(
      expectedSentence({ primary: "answer", value: { answer: "Refund $129" } }),
    ).toBe("Refund $129");
    expect(expectedSentence(null)).toBe("");
  });
});

describe("trialOutcome + checklist", () => {
  const result = {
    scores: {
      "eval:task_success": {
        trials: [0, 1, 0],
        reason: "Order found failed; later evals were not reached.",
      },
    },
  };
  it("labels k trials and one trial", () => {
    expect(trialOutcome(result, "eval:task_success")).toMatchObject({
      pass: 1,
      n: 3,
      tone: "flaky",
      label: "1/3 trials",
    });
    expect(trialOutcome({ scores: { k: { value: 1 } } }, "k")).toMatchObject({
      label: "Pass",
      tone: "pass",
    });
    expect(trialOutcome({}, "k").label).toBe("Not scored");
  });
  it("ticks checks before the failed one, crosses it, leaves the rest not reached", () => {
    const list = expectedChecklist(EXPECTED, result, {
      trial: 1,
      metricKey: "eval:task_success",
    });
    expect(list.map((c) => c.passed)).toEqual([false, null, null, null]);
    const passed = expectedChecklist(EXPECTED, result, {
      trial: 2,
      metricKey: "eval:task_success",
    });
    expect(passed.every((c) => c.passed === true)).toBe(true);
  });
  it("picks the pass/fail eval", () => {
    expect(
      primaryPassMetric([
        { key: "x", kind: "system" },
        { key: "eval:task_success", kind: "eval", output_type: "pass_fail" },
      ]).key,
    ).toBe("eval:task_success");
  });
});

describe("whyChips", () => {
  it("names the worst movement first", () => {
    const row = {
      results: {
        B: { scores: { t: { display: "3/3" } } },
        A: {
          movement: { t: "regressed" },
          delta: { t: -1 },
          scores: { t: { display: "0/3" } },
        },
        C: {
          movement: { t: "improved" },
          delta: { t: 0.3 },
          scores: { t: { display: "3/3" } },
        },
      },
    };
    const chips = whyChips(row, {
      runIds: ["B", "A", "C"],
      baselineId: "B",
      defsByKey: { t: { name: "Task success", kind: "eval" } },
      runsById: { A: { label: "#2A" }, C: { label: "#2C" } },
    });
    expect(chips[0]).toMatchObject({
      tone: "bad",
      text: "#2A Task success 3/3 → 0/3",
    });
    expect(chips).toHaveLength(2);
  });
});

describe("highlightParts (RC#11)", () => {
  it("marks every case-insensitive match", async () => {
    const { highlightParts } = await import("../cells/cellCommon");
    expect(highlightParts("Order A-8830 and a-8830", "a-8830")).toEqual([
      { text: "Order ", hit: false },
      { text: "A-8830", hit: true },
      { text: " and ", hit: false },
      { text: "a-8830", hit: true },
    ]);
    expect(highlightParts("abc", "")).toEqual([{ text: "abc", hit: false }]);
  });
});
