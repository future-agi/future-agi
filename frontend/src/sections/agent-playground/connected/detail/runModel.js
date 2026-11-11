import { paths } from "src/routes/paths";

// TestExecution.ExecutionStatus: pending, running, evaluating, cancelling,
// completed, failed, cancelled.
const IN_PROGRESS = ["pending", "running", "evaluating", "cancelling"];

export const isRunning = (run) => IN_PROGRESS.includes(run.status);

// "agent v3 × environment v7": what a run held fixed, named the same way
// everywhere. A run recorded before versioning may know only one side.
export const pairLabel = (run) =>
  [
    run.agentVersion && `agent v${run.agentVersion.number}`,
    run.environmentVersion && `environment v${run.environmentVersion.number}`,
  ]
    .filter(Boolean)
    .join(" × ") || null;

export const runPath = (run) =>
  run.kind === "environment" && run.environment
    ? paths.dashboard.simulate.environments.execution(
        run.environment.id,
        run.runTestId,
        run.testExecutionId,
      )
    : `${paths.dashboard.simulate.test}/${run.runTestId}/${run.testExecutionId}`;
