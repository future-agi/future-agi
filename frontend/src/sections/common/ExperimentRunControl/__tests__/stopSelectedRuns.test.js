// One run's own Stop asks run control for that run only. It never falls back
// to the empty-body stop (that would stop every run of the experiment); once
// the server has said run control is off it sends nothing.
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({ stopRuns: vi.fn(), resumeRuns: vi.fn() }));
vi.mock("src/api/develop/experiment-run-actions", () => ({
  stopRuns: calls.stopRuns,
  resumeRuns: calls.resumeRuns,
}));

import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import {
  RUN_CONTROL_NOT_ENABLED,
  RUN_RESUME_HELD,
  RUN_STOP_HELD,
  noteRunControl,
  resetRunControlForTest,
  runControlState,
  stopSelectedRuns,
} from "../runControlAvailability";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const RUN = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e4f";
const notEnabled = () => ({
  status: false,
  code: RUN_CONTROL_NOT_ENABLED,
  detail: "Run control is not available.",
  statusCode: 404,
});
const accepted = { status: 202, data: { status: true, result: { runs: [] } } };

beforeEach(() => {
  resetRunControlForTest();
  calls.stopRuns.mockReset();
});

describe("stopSelectedRuns", () => {
  it("records the refusal and rethrows it without stopping the whole experiment", async () => {
    const error = notEnabled();
    calls.stopRuns.mockRejectedValueOnce(error);
    await expect(stopSelectedRuns(EXP, [RUN])).rejects.toBe(error);
    expect(calls.stopRuns.mock.calls).toEqual([[EXP, [RUN]]]);
    expect(runControlState()).toBe("off");
  });

  it("sends nothing once the server said run control is off", async () => {
    noteRunControl("off");
    await expect(stopSelectedRuns(EXP, [RUN])).resolves.toBeNull();
    expect(calls.stopRuns).not.toHaveBeenCalled();
  });

  it("stops the named run where run control answers", async () => {
    calls.stopRuns.mockResolvedValue(accepted);
    await expect(stopSelectedRuns(EXP, [RUN])).resolves.toBe(accepted);
    expect(calls.stopRuns.mock.calls).toEqual([[EXP, [RUN]]]);
    expect(runControlState()).toBe("on");
  });

  it("rethrows any other refusal and stays unknown", async () => {
    const error = {
      statusCode: 404,
      code: "not_found",
      detail: "Run not found.",
    };
    calls.stopRuns.mockRejectedValueOnce(error);
    await expect(stopSelectedRuns(EXP, [RUN])).rejects.toBe(error);
    expect(calls.stopRuns).toHaveBeenCalledTimes(1);
    expect(runControlState()).toBe("unknown");
  });
});

describe("the reasons a held run action shows", () => {
  it("say the shared line, and where the experiment still stops", () => {
    expect(RUN_RESUME_HELD).toBe(NOT_IN_RELEASE);
    expect(RUN_STOP_HELD).toBe(
      `${NOT_IN_RELEASE}. Stop the whole experiment from its page.`,
    );
  });
});
