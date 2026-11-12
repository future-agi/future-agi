// What the page learns about run control comes only from run control's own
// answers: the 404 "run_control_not_enabled" refusal, or a success. Stop falls
// back to the empty-body stop on that refusal and on nothing else, so a
// selected-run stop is never widened by any other error.
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({ stopRuns: vi.fn(), resumeRuns: vi.fn() }));
vi.mock("src/api/develop/experiment-run-actions", () => ({
  stopRuns: calls.stopRuns,
  resumeRuns: calls.resumeRuns,
}));

import {
  RUN_CONTROL_NOT_ENABLED,
  isRunControlNotEnabled,
  noteRunControl,
  resetRunControlForTest,
  resumeExperimentRuns,
  runControlState,
  stopExperimentRuns,
  useRunControlState,
} from "../runControlAvailability";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const RUN = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e4f";

// The API client rejects with the response body spread and its statusCode.
const refusal = (statusCode, code, detail = "Refused.") => ({
  status: false,
  code,
  detail,
  message: detail,
  statusCode,
});
const notEnabled = () =>
  refusal(404, RUN_CONTROL_NOT_ENABLED, "Run control is not available.");
const accepted = { status: 202, data: { status: true, result: { runs: [] } } };
const cancelled = { status: 200, data: { status: true, result: {} } };

beforeEach(() => {
  resetRunControlForTest();
  calls.stopRuns.mockReset();
  calls.resumeRuns.mockReset();
});

describe("isRunControlNotEnabled", () => {
  it("reads the client's error and a raw Axios error", () => {
    expect(isRunControlNotEnabled(notEnabled())).toBe(true);
    expect(
      isRunControlNotEnabled({
        code: "ERR_BAD_REQUEST",
        response: { status: 404, data: { code: RUN_CONTROL_NOT_ENABLED } },
      }),
    ).toBe(true);
  });

  it.each([
    [
      "a run the server cannot find",
      refusal(404, "not_found", "Run not found."),
    ],
    ["a run that cannot be controlled", refusal(409, "conflict")],
    ["a temporarily unavailable control", refusal(503, "service_unavailable")],
    ["the code on another status", refusal(400, RUN_CONTROL_NOT_ENABLED)],
    [
      "a network failure",
      { message: "Network Error", transportCode: "ERR_NETWORK" },
    ],
    ["a thrown Error", new Error("Select at least one run to stop.")],
    ["nothing", null],
  ])("is false for %s", (_, error) => {
    expect(isRunControlNotEnabled(error)).toBe(false);
  });
});

describe("stopExperimentRuns", () => {
  it("falls back to the empty-body stop only on the not-enabled refusal", async () => {
    calls.stopRuns
      .mockRejectedValueOnce(notEnabled())
      .mockResolvedValueOnce(cancelled);
    const result = await stopExperimentRuns(EXP, [RUN]);
    expect(calls.stopRuns.mock.calls).toEqual([[EXP, [RUN]], [EXP]]);
    expect(result).toEqual({ response: cancelled, whole: true });
    expect(runControlState()).toBe("off");
  });

  it("sends only the empty-body stop once the server said run control is off", async () => {
    noteRunControl("off");
    calls.stopRuns.mockResolvedValue(cancelled);
    const result = await stopExperimentRuns(EXP, [RUN]);
    expect(calls.stopRuns.mock.calls).toEqual([[EXP]]);
    expect(result.whole).toBe(true);
  });

  it("keeps the run-id stop when run control answers", async () => {
    calls.stopRuns.mockResolvedValue(accepted);
    const result = await stopExperimentRuns(EXP, [RUN]);
    expect(calls.stopRuns.mock.calls).toEqual([[EXP, [RUN]]]);
    expect(result).toEqual({ response: accepted, whole: false });
    expect(runControlState()).toBe("on");
  });

  it.each([
    [
      "a run the server cannot find",
      refusal(404, "not_found", "Run not found."),
    ],
    ["a run that cannot be controlled", refusal(409, "conflict")],
    ["a temporarily unavailable control", refusal(503, "service_unavailable")],
    [
      "a network failure",
      { message: "Network Error", transportCode: "ERR_NETWORK" },
    ],
  ])("never widens the stop for %s", async (_, error) => {
    calls.stopRuns.mockRejectedValueOnce(error);
    await expect(stopExperimentRuns(EXP, [RUN])).rejects.toBe(error);
    expect(calls.stopRuns.mock.calls).toEqual([[EXP, [RUN]]]);
    expect(runControlState()).toBe("unknown");
  });

  it("reports a failed empty-body stop instead of a stopped experiment", async () => {
    const failure = refusal(
      400,
      "validation_error",
      "Experiment is not running.",
    );
    calls.stopRuns
      .mockRejectedValueOnce(notEnabled())
      .mockRejectedValueOnce(failure);
    await expect(stopExperimentRuns(EXP, [RUN])).rejects.toBe(failure);
    expect(calls.stopRuns.mock.calls).toEqual([[EXP, [RUN]], [EXP]]);
  });
});

describe("resumeExperimentRuns", () => {
  it("records the refusal, rethrows it, then sends nothing", async () => {
    const error = notEnabled();
    calls.resumeRuns.mockRejectedValueOnce(error);
    await expect(resumeExperimentRuns(EXP, [RUN])).rejects.toBe(error);
    expect(runControlState()).toBe("off");
    await expect(resumeExperimentRuns(EXP, [RUN])).resolves.toBeNull();
    expect(calls.resumeRuns).toHaveBeenCalledTimes(1);
  });

  it("resumes the named runs when run control answers", async () => {
    calls.resumeRuns.mockResolvedValue(accepted);
    await expect(resumeExperimentRuns(EXP, [RUN])).resolves.toBe(accepted);
    expect(calls.resumeRuns.mock.calls).toEqual([[EXP, [RUN]]]);
    expect(runControlState()).toBe("on");
  });
});

describe("useRunControlState", () => {
  it("starts unknown and follows what the server said", () => {
    const { result } = renderHook(() => useRunControlState());
    expect(result.current).toBe("unknown");
    act(() => noteRunControl("off"));
    expect(result.current).toBe("off");
    act(() => noteRunControl("on"));
    expect(result.current).toBe("on");
    act(() => noteRunControl("maybe"));
    expect(result.current).toBe("on");
  });
});
