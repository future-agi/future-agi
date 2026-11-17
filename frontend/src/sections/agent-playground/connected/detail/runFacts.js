import { paths } from "src/routes/paths";

const IN_PROGRESS = ["pending", "running", "evaluating", "cancelling"];

export const isRunning = (run) => IN_PROGRESS.includes(run.status);

export const runPath = (run) =>
  run.kind === "environment" && run.environment
    ? paths.dashboard.simulate.environments.execution(
        run.environment.id,
        run.runTestId,
        run.testExecutionId,
      )
    : `${paths.dashboard.simulate.test}/${run.runTestId}/${run.testExecutionId}`;

// The trials the rate leaves out, as the server counts them.
export const unratedOf = (run) => {
  const left = [
    run.trialsInconclusive ? `${run.trialsInconclusive} inconclusive` : null,
    run.trialsExcluded ? `${run.trialsExcluded} not comparable` : null,
  ].filter(Boolean);
  return left.length ? `${left.join(", ")}, not in the rate` : "";
};
