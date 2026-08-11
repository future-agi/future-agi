import { describe, it, expect } from "vitest";

import {
  FEEDBACK_OUTPUT_TYPES,
  getCurrentValue,
  getReason,
  serializeFeedbackValue,
  toArray,
} from "./feedback_value";
import { feedbackFormSchema } from "./validation";

describe("feedback_value helpers", () => {
  describe("toArray — multi-choice values", () => {
    it("parses a JSON-encoded array string", () => {
      expect(toArray('["A","B"]')).toEqual(["A", "B"]);
    });

    it("wraps a plain string as a single-item array", () => {
      expect(toArray("A")).toEqual(["A"]);
    });

    it("passes arrays through and treats nullish as empty", () => {
      expect(toArray(["A", "B"])).toEqual(["A", "B"]);
      expect(toArray("")).toEqual([]);
      expect(toArray(null)).toEqual([]);
      expect(toArray(undefined)).toEqual([]);
    });
  });

  describe("getReason — eval explanation for the cell", () => {
    it("reads reason from valueInfos or value_infos, falling back to summary", () => {
      expect(getReason({ valueInfos: { reason: "because" } })).toBe("because");
      expect(getReason({ value_infos: { summary: "sum" } })).toBe("sum");
      expect(getReason({})).toBe("");
    });

    it("parses value_infos when it arrives as a JSON string (experiment shape)", () => {
      expect(
        getReason({ value_infos: '{"reason":"stringified"}' }),
      ).toBe("stringified");
    });

    it("falls back to metadata.reason for the experiment shape", () => {
      expect(getReason({ metadata: { reason: "meta reason" } })).toBe(
        "meta reason",
      );
      // value_infos reason still wins over metadata when present
      expect(
        getReason({
          value_infos: { reason: "primary" },
          metadata: { reason: "meta" },
        }),
      ).toBe("primary");
    });

    it("returns empty string for unparseable value_infos with no metadata", () => {
      expect(getReason({ value_infos: "not json" })).toBe("");
    });
  });

  describe("serializeFeedbackValue — API value encoding", () => {
    it("JSON-encodes multi-choice arrays", () => {
      expect(serializeFeedbackValue(["A", "B"])).toBe('["A","B"]');
    });

    it("passes scalar values through unchanged", () => {
      expect(serializeFeedbackValue("Passed")).toBe("Passed");
      expect(serializeFeedbackValue(59)).toBe(59);
    });
  });

  describe("getCurrentValue: display of the eval's current output", () => {
    it("returns empty string when the cell has no value", () => {
      expect(getCurrentValue({}, null)).toBe("");
      expect(getCurrentValue({ value: null }, null)).toBe("");
      expect(getCurrentValue({ value: undefined }, null)).toBe("");
      expect(getCurrentValue({ value: "" }, null)).toBe("");
    });

    it("returns the raw value as a string when choice_scores is absent", () => {
      expect(getCurrentValue({ value: 0.5 }, null)).toBe("0.5");
      expect(getCurrentValue({ value: "Resolved" }, undefined)).toBe("Resolved");
      // Score of 0 must still render as "0", not be treated as absent.
      expect(getCurrentValue({ value: 0 }, null)).toBe("0");
    });

    it("annotates the choice with its derived score when choice_scores maps", () => {
      const map = { Yes: 1.0, No: 0.0 };
      expect(getCurrentValue({ value: "Yes" }, map)).toBe("Yes (score 1)");
      expect(getCurrentValue({ value: "No" }, map)).toBe("No (score 0)");
    });

    it("falls back to the raw value when the label is not in choice_scores", () => {
      expect(
        getCurrentValue({ value: "Unmapped" }, { Yes: 1.0, No: 0.0 }),
      ).toBe("Unmapped");
    });

    it("extracts choices out of {score, choices: [...]} object values", () => {
      expect(
        getCurrentValue(
          { value: { score: 0.3, choices: ["Toxic", "Abrupt"] } },
          null,
        ),
      ).toBe("Toxic, Abrupt");
    });

    it("parses Python-repr with choices via the shared normalizer", () => {
      expect(
        getCurrentValue(
          { value: "{'score': 0.3, 'choices': ['Toxic', 'Abrupt']}" },
          null,
        ),
      ).toBe("Toxic, Abrupt");
    });

    it("parses JSON-encoded arrays into a comma-separated list", () => {
      expect(getCurrentValue({ value: '["A","B","C"]' }, null)).toBe("A, B, C");
    });

    it("returns a bare array as a comma-separated list", () => {
      expect(getCurrentValue({ value: ["A", "B"] }, null)).toBe("A, B");
    });

    it("unwraps single-choice {score, choice: 'Bad'} objects instead of stringifying to [object Object]", () => {
      // score-with-choices evals emit a single label + derived score; the
      // stored value round-trips through Python-repr so both raw dict and
      // repr string are exercised.
      expect(
        getCurrentValue({ value: { score: 0.0, choice: "Bad" } }, null),
      ).toBe("Bad");
      expect(
        getCurrentValue({ value: "{'score': 0.0, 'choice': 'Bad'}" }, null),
      ).toBe("Bad");
    });

    it("annotates unwrapped single-choice objects with choice_scores when mapped", () => {
      const map = { Bad: 0, Good: 1, Normal: 0.3, Average: 0.7 };
      expect(
        getCurrentValue({ value: { score: 0.0, choice: "Bad" } }, map),
      ).toBe("Bad (score 0)");
      expect(
        getCurrentValue({ value: "{'score': 0.7, 'choice': 'Average'}" }, map),
      ).toBe("Average (score 0.7)");
    });
  });

  it("exposes the expected output types", () => {
    expect(FEEDBACK_OUTPUT_TYPES.PASS_FAIL).toBe("Pass/Fail");
    expect(FEEDBACK_OUTPUT_TYPES.CHOICES).toBe("choices");
  });
});

describe("feedbackFormSchema — one-page submit gating", () => {
  it("accepts a complete feedback with a re-tune action", () => {
    const result = feedbackFormSchema.safeParse({
      value: "Passed",
      explanation: "The model missed the tool call.",
      actionType: "retune",
    });
    expect(result.success).toBe(true);
  });

  it("accepts a multi-choice array value", () => {
    const result = feedbackFormSchema.safeParse({
      value: ["A", "B"],
      explanation: "Both categories apply.",
      actionType: "recalculate_row",
    });
    expect(result.success).toBe(true);
  });

  it("requires a re-tune action to be selected", () => {
    const result = feedbackFormSchema.safeParse({
      value: "Passed",
      explanation: "Looks wrong.",
      actionType: "",
    });
    expect(result.success).toBe(false);
  });

  it("requires a value and an improvement note", () => {
    expect(
      feedbackFormSchema.safeParse({
        value: "",
        explanation: "note",
        actionType: "retune",
      }).success,
    ).toBe(false);
    expect(
      feedbackFormSchema.safeParse({
        value: "Passed",
        explanation: "",
        actionType: "retune",
      }).success,
    ).toBe(false);
    expect(
      feedbackFormSchema.safeParse({
        value: [],
        explanation: "note",
        actionType: "retune",
      }).success,
    ).toBe(false);
  });
});
