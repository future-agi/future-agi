// A run's Stop on the Experiments list. Run control on: only that run
// pauses. Run control off: the experiment's runs are read again at the
// click, after the server's refusal, and the run is stopped with the
// empty-body stop only when it is the one run going; with another run going
// nothing more is sent and the answer is the sentence to show. Any other
// error never becomes the empty-body stop.
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({
  log: [],
  stopRuns: vi.fn(),
  resumeRuns: vi.fn(),
}));
vi.mock("src/api/develop/experiment-run-actions", () => ({
  stopRuns: (...args) => calls.stopRuns(...args),
  resumeRuns: (...args) => calls.resumeRuns(...args),
}));

import {
  RUN_CONTROL_NOT_ENABLED,
  noteRunControl,
  resetRunControlForTest,
  runControlState,
  stopOneRun,
} from "../runControlAvailability";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const RUN = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e05";
const OTHER = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e06";
const SENTENCE =
  "This run can't be stopped on its own while other runs of this experiment are going. Open the experiment to stop all of its runs.";

const notEnabled = () => ({
  status: false,
  code: RUN_CONTROL_NOT_ENABLED,
  detail: "Run control is not available.",
  statusCode: 404,
});
const run = (run_id, status) => ({
  run_id,
  experiment_id: EXP,
  status,
  progress: { total: 40, done: 2, errors: 0 },
});
const wholeStopped = {
  status: 200,
  data: { status: true, result: { experiment_id: EXP, rows_in_flight: 0 } },
};
const paused = {
  status: 202,
  data: { status: true, result: { runs: [{ run_id: RUN, done: 4 }] } },
};

// The stand-in server: run control on or off; every call is logged in
// order, the read included.
let runControl = false;
let runsNow = [];
const readRuns = vi.fn(async () => {
  calls.log.push("read");
  return runsNow;
});

beforeEach(() => {
  resetRunControlForTest();
  runControl = false;
  runsNow = [run(RUN, "Running")];
  calls.log.length = 0;
  readRuns.mockClear();
  calls.stopRuns.mockReset();
  calls.stopRuns.mockImplementation(async (experimentId, runIds) => {
    calls.log.push(runIds === undefined ? "stop {}" : `stop ${runIds}`);
    if (runIds !== undefined && !runControl) throw notEnabled();
    return runIds === undefined ? wholeStopped : paused;
  });
});

describe("stopOneRun, run control off", () => {
  it("alone at the click: the named stop, the read, then the empty-body stop", async () => {
    const out = await stopOneRun(EXP, RUN, readRuns);
    expect(calls.log).toEqual([`stop ${RUN}`, "read", "stop {}"]);
    expect(out.whole).toBe(true);
    expect(out.response).toBe(wholeStopped);
    expect(out.run.run_id).toBe(RUN);
    expect(runControlState()).toBe("off");
  });

  it("another run started since the list was read: no empty-body stop, the sentence", async () => {
    runsNow = [run(RUN, "Running"), run(OTHER, "Queued")];
    const out = await stopOneRun(EXP, RUN, readRuns);
    expect(calls.log).toEqual([`stop ${RUN}`, "read"]);
    expect(out).toEqual({ refused: SENTENCE });
  });

  it("once the page knows: only the read, then the empty-body stop when alone", async () => {
    noteRunControl("off");
    const out = await stopOneRun(EXP, RUN, readRuns);
    expect(calls.log).toEqual(["read", "stop {}"]);
    expect(out.whole).toBe(true);
  });

  it("once the page knows and another run is going: only the read", async () => {
    noteRunControl("off");
    runsNow = [run(RUN, "Running"), run(OTHER, "Running")];
    await expect(stopOneRun(EXP, RUN, readRuns)).resolves.toEqual({
      refused: SENTENCE,
    });
    expect(calls.log).toEqual(["read"]);
  });

  it("the run ended before the click reached it: nothing is stopped", async () => {
    runsNow = [run(RUN, "Completed"), run(OTHER, "Completed")];
    await expect(stopOneRun(EXP, RUN, readRuns)).resolves.toEqual({
      ended: true,
    });
    expect(calls.log).toEqual([`stop ${RUN}`, "read"]);
  });
});

describe("stopOneRun, run control on or another refusal", () => {
  it("run control on: the named run pauses, nothing is read", async () => {
    runControl = true;
    const out = await stopOneRun(EXP, RUN, readRuns);
    expect(calls.log).toEqual([`stop ${RUN}`]);
    expect(out).toEqual({ response: paused, whole: false });
    expect(readRuns).not.toHaveBeenCalled();
    expect(runControlState()).toBe("on");
  });

  it("any other error is thrown, never turned into the empty-body stop", async () => {
    const error = {
      statusCode: 404,
      code: "not_found",
      detail: "Run not found.",
    };
    calls.stopRuns.mockRejectedValueOnce(error);
    await expect(stopOneRun(EXP, RUN, readRuns)).rejects.toBe(error);
    expect(calls.stopRuns).toHaveBeenCalledTimes(1);
    expect(readRuns).not.toHaveBeenCalled();
    expect(runControlState()).toBe("unknown");
  });

  it("a failed read is thrown, and nothing more is sent", async () => {
    const error = new Error("read failed");
    readRuns.mockRejectedValueOnce(error);
    await expect(stopOneRun(EXP, RUN, readRuns)).rejects.toBe(error);
    expect(calls.stopRuns).toHaveBeenCalledTimes(1);
    expect(calls.stopRuns.mock.calls[0][1]).toEqual([RUN]);
  });
});
