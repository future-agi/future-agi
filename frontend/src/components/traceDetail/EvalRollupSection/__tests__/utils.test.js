import { describe, it, expect } from "vitest";
import {
  spanResultChip,
  spanPassed,
  spanHasDetail,
  colFromEval,
} from "../utils";

describe("spanResultChip", () => {
  // Failure path first
  it("renders an errored span as Errored", () => {
    expect(spanResultChip({ error: true, value: null }, "score")).toEqual({
      label: "Errored",
      tone: "errored",
    });
  });

  // Numeric — color by value (the dev-parity fix)
  it("colors a numeric span by value (red <40, green >=60)", () => {
    expect(spanResultChip({ value: 30 }, "score")).toEqual({
      label: "30%",
      tone: "fail",
    });
    expect(spanResultChip({ value: 80 }, "score")).toEqual({
      label: "80%",
      tone: "pass",
    });
  });

  // Boundary / null
  it("renders a missing numeric value as a plain dash", () => {
    expect(spanResultChip({ value: null }, "score")).toEqual({
      label: "—",
      tone: "plain",
    });
  });

  it("renders pass/fail spans", () => {
    expect(spanResultChip({ value: "pass" }, "Pass/Fail")).toEqual({
      label: "Pass",
      tone: "pass",
    });
    expect(spanResultChip({ value: "fail" }, "Pass/Fail")).toEqual({
      label: "Fail",
      tone: "fail",
    });
  });

  it("renders a choice span's labels", () => {
    expect(spanResultChip({ value: ["anger"] }, "choices")).toEqual({
      label: "anger",
      tone: "neutral",
    });
  });

  it("colors a choice label from the passed choicesMap", () => {
    expect(
      spanResultChip({ value: ["anger"] }, "choices", { anger: "fail" }),
    ).toEqual({ label: "anger", tone: "fail" });
  });
});

describe("spanPassed", () => {
  it("is false for an errored span", () => {
    expect(spanPassed({ error: true, value: 90 }, "score")).toBe(false);
  });
  it("uses the >=50 threshold for numeric", () => {
    expect(spanPassed({ value: 50 }, "score")).toBe(true);
    expect(spanPassed({ value: 49 }, "score")).toBe(false);
  });
  it("treats choices as always passed (nothing to fix)", () => {
    expect(spanPassed({ value: ["anger"] }, "choices")).toBe(true);
  });
  it("passes only on an explicit pass for pass/fail", () => {
    expect(spanPassed({ value: "pass" }, "Pass/Fail")).toBe(true);
    expect(spanPassed({ value: "fail" }, "Pass/Fail")).toBe(false);
  });
});

describe("spanHasDetail", () => {
  it("expands when there is an explanation or error, regardless of pass", () => {
    expect(spanHasDetail({ value: 90, explanation: "why" }, "score")).toBe(
      true,
    );
    expect(spanHasDetail({ error: true, value: null }, "score")).toBe(true);
  });
  it("expands a failed eval with no explanation so Fix-with-Falcon is reachable", () => {
    expect(spanHasDetail({ value: 30 }, "score")).toBe(true);
    expect(spanHasDetail({ value: "fail" }, "Pass/Fail")).toBe(true);
  });
  it("stays collapsed for a passing eval with no explanation", () => {
    expect(spanHasDetail({ value: 80 }, "score")).toBe(false);
    expect(spanHasDetail({ value: "pass" }, "Pass/Fail")).toBe(false);
  });
  it("stays collapsed for choice evals (nothing to fix)", () => {
    expect(spanHasDetail({ value: ["anger"] }, "choices")).toBe(false);
  });
});

describe("colFromEval", () => {
  it("shims an eval into the col shape evalCellChips expects", () => {
    expect(
      colFromEval({
        eval_config_id: "c1",
        eval_name: "tone",
        output_type: "choices",
      }),
    ).toEqual({
      id: "c1",
      name: "tone",
      outputType: "choices",
      choicesMap: {},
    });
  });

  it("carries the eval's choices_map through when present", () => {
    expect(
      colFromEval({
        eval_config_id: "c1",
        eval_name: "tone",
        output_type: "choices",
        choices_map: { anger: "fail", joy: "pass" },
      }),
    ).toEqual({
      id: "c1",
      name: "tone",
      outputType: "choices",
      choicesMap: { anger: "fail", joy: "pass" },
    });
  });
});
