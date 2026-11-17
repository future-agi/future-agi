// options-truth L3 G3 (R4): status words of cells a run never reached.
import { describe, expect, it } from "vitest";
import {
  carriedLabel,
  notScoredReason,
  queuedLabel,
  stoppedRun,
} from "../cellCommon";

describe("queuedLabel", () => {
  it("a run stopped at its cost cap never runs its other rows", () => {
    expect(
      queuedLabel({ status: "Cancelled", stop_reason: "Stopped at cost cap" }),
    ).toBe("Not run · stopped at cost cap");
    expect(
      queuedLabel({
        status: "Cancelled",
        status_detail: "Stopped at cost cap",
      }),
    ).toBe("Not run · stopped at cost cap");
    expect(queuedLabel({ status: "Stopped" })).toBe("Not run · stopped");
    expect(queuedLabel({ status: "Running" })).toBe("Queued");
    expect(queuedLabel({ status: "WaitingForResults" })).toBe(
      "Waiting for results",
    );
    expect(stoppedRun({ status: "Completed" })).toBe(false);
  });
});

describe("notScoredReason", () => {
  it("names a check that was not reached", () => {
    expect(
      notScoredReason({
        run: { status: "Completed" },
        row: {},
        def: {},
        score: { not_reached: true },
      }),
    ).toBe("not reached: an earlier check failed");
    expect(
      notScoredReason({ run: { status: "Running" }, row: {}, def: {} }),
    ).toBe("run not finished");
  });
});

describe("carriedLabel", () => {
  it("names the run a carried result came from", () => {
    expect(carriedLabel({ carried_from: "#1A" })).toBe("Carried from #1A");
    expect(carriedLabel({})).toBeNull();
  });
});
