import { canResumeRun } from "../Runs/runResume";
import { getRunStatus } from "../Runs/runStatus";
import { runKindOf } from "../Runs/runKind";

export const RESUME_BEFORE_RERUN =
  "Resume the paused runs to finish remaining rows before rerunning.";
export const FINISH_STOP_BEFORE_RERUN =
  "Stopping didn't finish; press Stop again to complete it before rerunning.";

export const resumableRunsOf = (runs = []) =>
  runs.filter(
    (run) =>
      run.completed_at == null &&
      canResumeRun({ ...run, status: getRunStatus(run).key }),
  );

export const needsLegacyStop = (runs = [], runControl) =>
  runControl === "off" &&
  runs.some(
    (run) =>
      run.native_config_id &&
      run.completed_at === null &&
      getRunStatus(run).key === "Cancelled" &&
      runKindOf(run) !== "http",
  );

export const rerunPauseReason = (runs, runControl) =>
  needsLegacyStop(runs, runControl)
    ? FINISH_STOP_BEFORE_RERUN
    : runControl !== "off" && resumableRunsOf(runs).length > 0
      ? RESUME_BEFORE_RERUN
      : null;
