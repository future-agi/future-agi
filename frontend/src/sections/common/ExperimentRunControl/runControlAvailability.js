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
