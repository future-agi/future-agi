import { describe, it, expect } from "vitest";

import { CHECK_KINDS, checkKind } from "../traceTable.constants";

describe("checkKind", () => {
  it.each([
    [{ result: "fail" }, {}, "task"],
    [{ passed: false }, {}, "task"],
    [{ result: "unmeasured" }, {}, "evidence"],
    [{ result: "inconclusive" }, {}, "grader"],
    [
      { result: null },
      { failureCode: "check_broken", failureDomain: "grading" },
      "grader",
    ],
    [{ result: null }, { failureDomain: "infrastructure" }, "infrastructure"],
    [{ result: null }, { failureDomain: "connectivity" }, "infrastructure"],
  ])("names %j on %j as %s", (goal, call, kind) => {
    expect(checkKind(goal, call)).toBe(kind);
    expect(CHECK_KINDS[kind]).toBeTruthy();
  });

  it("pass is none of them, and an agent-side failure with no result is not infrastructure", () => {
    expect(
      checkKind({ result: "pass" }, { failureDomain: "infrastructure" }),
    ).toBeNull();
    expect(checkKind({ passed: true }, {})).toBeNull();
    expect(checkKind({ result: "not_applicable" }, {})).toBeNull();
    expect(checkKind({ result: null }, { failureDomain: "agent" })).toBeNull();
  });
});
