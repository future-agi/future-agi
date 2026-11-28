import { describe, expect, it } from "vitest";

import {
  DEBUG_FAILURES_REFUSALS,
  NOTHING_FAILED_REASON,
  TOO_FEW_CALLS_REASON,
  debugFailuresBlockedReason,
  debugFailuresRefusalReason,
} from "../debugFailuresRule";

describe("debugFailuresBlockedReason", () => {
  it.each([
    [{ connectedCalls: 0, failingCalls: 0 }, TOO_FEW_CALLS_REASON],
    [{ connectedCalls: 14, failingCalls: 3 }, TOO_FEW_CALLS_REASON],
    [{ connectedCalls: 15, failingCalls: 0 }, NOTHING_FAILED_REASON],
    [{ connectedCalls: 15, failingCalls: 1 }, null],
    [{ connectedCalls: 200, failingCalls: 40 }, null],
    // A count the server did not send blocks nothing; the server still refuses.
    [{ connectedCalls: undefined, failingCalls: 0 }, NOTHING_FAILED_REASON],
    [{ connectedCalls: null, failingCalls: null }, null],
    [{}, null],
  ])("%o -> %s", (counts, reason) => {
    expect(debugFailuresBlockedReason(counts)).toBe(reason);
  });

  it("says each reason in product words", () => {
    expect(TOO_FEW_CALLS_REASON).toBe(
      "You need at least 15 connected calls to debug failures.",
    );
    expect(NOTHING_FAILED_REASON).toBe(
      "Nothing failed in this run, so there’s nothing to debug.",
    );
  });

  it("maps the server's refusal codes to the same reasons", () => {
    expect(DEBUG_FAILURES_REFUSALS).toEqual({
      investigation_too_few_calls: TOO_FEW_CALLS_REASON,
      investigation_nothing_failed: NOTHING_FAILED_REASON,
    });
  });
});

describe("debugFailuresRefusalReason", () => {
  // Any other conflict is a generic failure, never a reason the run gives.
  it.each(["conflict", "error", "", null, undefined])(
    "%o is not a refusal",
    (code) => {
      expect(debugFailuresRefusalReason(code)).toBeNull();
    },
  );
});
