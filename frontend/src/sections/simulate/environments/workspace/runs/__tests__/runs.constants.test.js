import { describe, expect, it } from "vitest";

import { STATUS_META, runStateFor } from "../runs.constants";

describe("STATUS_META", () => {
  it("gives the header's mixed-result Completed the same role as a finished run", () => {
    expect(STATUS_META.completed.role).toBe(STATUS_META.finished.role);
  });

  it("keeps an all-failed run in the danger role", () => {
    expect(STATUS_META.failed.role).toBe("danger");
  });
});

describe("runStateFor", () => {
  it("shows a run whose calls are done but still scoring as Grading, not Running", () => {
    expect(runStateFor("evaluating")).toBe("grading");
    expect(STATUS_META.grading.label).toBe("Grading");
  });

  it("reads a stopped run as Stop requested until the agent's side confirms it, then Stopped", () => {
    expect(runStateFor("cancelling", { status: "requested" })).toBe(
      "stop_requested",
    );
    expect(runStateFor("cancelled", { status: "requested" })).toBe(
      "stop_requested",
    );
    expect(runStateFor("cancelled", { status: "confirmed" })).toBe("stopped");
    expect(STATUS_META.stop_requested.label).toBe("Stop requested");
    expect(STATUS_META.stopped.label).toBe("Stopped");
  });

  it("shows Stop not confirmed in amber and Cleanup failed in red", () => {
    expect(runStateFor("cancelled", { status: "unconfirmed" })).toBe(
      "stop_unconfirmed",
    );
    expect(STATUS_META.stop_unconfirmed).toEqual({
      role: "warning",
      label: "Stop not confirmed",
    });
    expect(runStateFor("cancelled", { status: "cleanup_failed" })).toBe(
      "cleanup_failed",
    );
    expect(STATUS_META.cleanup_failed.role).toBe("danger");
  });

  it("keeps Cancelled for a stopped run with no stop record, and ignores a stop on a live run", () => {
    expect(runStateFor("cancelled", null)).toBe("cancelled");
    expect(runStateFor("running", { status: "requested" })).toBe("running");
  });
});
