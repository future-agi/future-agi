import { describe, it, expect } from "vitest";

import {
  FEEDBACK_OUTPUT_TYPES,
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
