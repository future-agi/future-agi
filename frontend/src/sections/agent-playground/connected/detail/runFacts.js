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

export const unratedOf = (run) =>
  [
    run.errored ? `${run.errored} errored` : null,
    run.noScore ? `${run.noScore} no score, not in the rate` : null,
  ]
    .filter(Boolean)
    .join(", ");
