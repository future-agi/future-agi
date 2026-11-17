// Whether this API lets a user pause and resume single runs (run control).
// No response the frontend already reads carries the server's setting, so
// the answer comes from run control's own requests: the stop that names
// runs, resume and the run-control read answer 404 with the code below when
// the server has not turned run control on, before any lookup, workflow
// signal or write. Until one of them answers, the state is "unknown".
//
// The state lives for this page session only, never in browser storage: the
// server reads its setting on every request, so an operator can turn run
// control on with a restart and the next session learns it again.
import { useSyncExternalStore } from "react";
import { resumeRuns, stopRuns } from "src/api/develop/experiment-run-actions";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { getRunStatus } from "src/sections/common/Runs/runStatus";

/** The code the API sends when run control is not turned on. */
export const RUN_CONTROL_NOT_ENABLED = "run_control_not_enabled";

/**
 * Why one run's own Stop is held when run control is off, and where the
 * experiment still stops (its page's Stop cancels every run of it).
 */
export const RUN_STOP_HELD = `${NOT_IN_RELEASE}. Stop the whole experiment from its page.`;

/** Why one run's own Resume is held when run control is off. */
export const RUN_RESUME_HELD = NOT_IN_RELEASE;

/**
 * Why a run's Stop on the Experiments list stops nothing while run control
 * is off: other runs of its experiment are going, and the stop that does not
 * name runs would end them too. The experiment page's Stop ends them all.
 */
export const RUN_STOP_NOT_ALONE =
  "This run can't be stopped on its own while other runs of this experiment are going. Open the experiment to stop all of its runs.";

/** The run ended before its Stop reached it (the experiment page's words). */
export const RUN_NO_LONGER_RUNNING =
  "This run is no longer running; nothing was stopped.";

/**
 * Whether another run of `run`'s experiment is going in `runs` (the runs
 * the Experiments list shows; a Stop still reads them again at the click).
 */
export const otherRunsGoing = (runs, run) =>
  (runs ?? []).some(
    (other) =>
      other?.experiment_id === run?.experiment_id &&
      other.run_id !== run?.run_id &&
      getRunStatus(other).active,
  );

/**
 * True only for the server's own "run control is not on" refusal: a 404
 * carrying RUN_CONTROL_NOT_ENABLED. Reads the app client's error (the
 * response body spread with `statusCode`) and a raw Axios error.
 */
export function isRunControlNotEnabled(error) {
  if (!error || typeof error !== "object") return false;
  const status = error.statusCode ?? error.response?.status;
  if (status !== 404) return false;
  return (
    error.code === RUN_CONTROL_NOT_ENABLED ||
    error.response?.data?.code === RUN_CONTROL_NOT_ENABLED
  );
}

let state = "unknown";
const listeners = new Set();

const subscribe = (listener) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};
const snapshot = () => state;

/** Record what a run-control request just showed: "on" or "off". */
export function noteRunControl(next) {
  if (next !== "on" && next !== "off") return;
  if (next === state) return;
  state = next;
  listeners.forEach((listener) => listener());
}

/** "unknown" | "on" | "off", for code outside React. */
export const runControlState = () => state;

/** "unknown" | "on" | "off"; re-renders when a request changes it. */
export function useRunControlState() {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** Tests only: forget what the server said. */
export function resetRunControlForTest() {
  state = "unknown";
  listeners.forEach((listener) => listener());
}

/**
 * Stop every active run of one experiment.
 *
 * Run control on: the named runs pause and can resume (the request names
 * them, as before). Run control off: the server refuses that request with
 * RUN_CONTROL_NOT_ENABLED and changes nothing, so the experiment is then
 * stopped with the empty-body stop the classic page sends, which cancels its
 * workflows. Once the server has said "off", later stops send only the
 * empty-body stop. Any other error is the caller's to show; it never turns
 * into a whole-experiment stop.
 *
 * Callers pass every active run of the experiment: the empty-body stop
 * cancels the whole experiment, never one run of several.
 *
 * @param {string} experimentId
 * @param {string[]} runIds  every active run of the experiment
 * @returns {Promise<{response: object, whole: boolean}>}  `whole` is true
 *   when the empty-body stop cancelled the experiment (nothing to resume)
 */
export async function stopExperimentRuns(experimentId, runIds) {
  if (state === "off") {
    return { response: await stopRuns(experimentId), whole: true };
  }
  try {
    const response = await stopRuns(experimentId, runIds);
    noteRunControl("on");
    return { response, whole: false };
  } catch (error) {
    if (!isRunControlNotEnabled(error)) throw error;
    noteRunControl("off");
    return { response: await stopRuns(experimentId), whole: true };
  }
}

/**
 * Resume named runs. Sends nothing once the server has said run control is
 * off (resolves null); a refusal with RUN_CONTROL_NOT_ENABLED records "off"
 * and is rethrown for the caller to show.
 */
export async function resumeExperimentRuns(experimentId, runIds) {
  if (state === "off") return null;
  try {
    const response = await resumeRuns(experimentId, runIds);
    noteRunControl("on");
    return response;
  } catch (error) {
    if (isRunControlNotEnabled(error)) noteRunControl("off");
    throw error;
  }
}

/**
 * Stop only the named runs (one run's own Stop). It never falls back to the
 * empty-body stop: that cancels every run of the experiment, which is the
 * experiment page's Stop to do. Sends nothing once the server has said run
 * control is off (resolves null); a refusal with RUN_CONTROL_NOT_ENABLED
 * records "off" and is rethrown for the caller to show.
 */
export async function stopSelectedRuns(experimentId, runIds) {
  if (state === "off") return null;
  try {
    const response = await stopRuns(experimentId, runIds);
    noteRunControl("on");
    return response;
  } catch (error) {
    if (isRunControlNotEnabled(error)) noteRunControl("off");
    throw error;
  }
}

/**
 * A run's Stop on the Experiments list.
 *
 * Run control on: only the named run pauses (stopSelectedRuns), as before.
 * Run control off, known or just answered with RUN_CONTROL_NOT_ENABLED: the
 * experiment's runs are read again now (`readRuns`), never taken from the
 * list, which can be a poll behind. Only when this run is the experiment's
 * one active run is it stopped the way the experiment page stops it
 * (stopExperimentRuns, the empty-body stop). With another run going nothing
 * more is sent: the empty-body stop would end that run too. Any other error
 * is thrown for the caller to show; it never becomes the empty-body stop.
 *
 * @param {string} experimentId
 * @param {string} runId
 * @param {() => Promise<object[]>} readRuns  every run of the experiment,
 *   read from the server at the call
 * @returns {Promise<{response: object, whole: boolean, run?: object}
 *   | {refused: string} | {ended: true}>}  `refused` is the sentence to show
 *   (nothing stopped); `ended`: this run was no longer running
 */
export async function stopOneRun(experimentId, runId, readRuns) {
  if (state !== "off") {
    try {
      const response = await stopSelectedRuns(experimentId, [runId]);
      if (response !== null) return { response, whole: false };
    } catch (error) {
      if (!isRunControlNotEnabled(error)) throw error;
    }
  }
  // Every run in the read belongs to this experiment.
  const active = (await readRuns()).filter((r) => getRunStatus(r).active);
  const run = active.find((r) => r.run_id === runId);
  if (!run) return { ended: true };
  if (active.length > 1) return { refused: RUN_STOP_NOT_ALONE };
  return { ...(await stopExperimentRuns(experimentId, [runId])), run };
}
