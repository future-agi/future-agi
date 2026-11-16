import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import { CALL_WORKERS_COPY as C } from "./callTraces.constants";

/** Each worker's own checks, as served: beside the call's verdict, a failed one shown failed. */
export function WorkerChecks({ workers }) {
  return (
    <Stack spacing={0.5} data-testid="worker-checks">
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {C.workerChecks}
      </Typography>
      {workers.flatMap((worker) =>
        worker.checks.map((check) => (
          <Typography
            key={`${worker.worker_id}-${check.name}`}
            data-testid="worker-check"
            data-passed={String(check.passed)}
            sx={{
              typography: "s3",
              color: check.passed === false ? "error.main" : "text.secondary",
            }}
          >
            {worker.name} · {check.name} ·{" "}
            {C.workerCheckResult(check.passed, check.result)}
          </Typography>
        )),
      )}
    </Stack>
  );
}
WorkerChecks.propTypes = {
  workers: PropTypes.arrayOf(PropTypes.object).isRequired,
};

/** Each failed or unsure check on the step its evidence points to, or why it is on none. */
export function PlacedChecks({ placements, names }) {
  return (
    <Stack spacing={0.5} data-testid="placed-checks">
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {C.placedChecks}
      </Typography>
      {placements.map((one, index) => {
        // A missing worker is Unknown, never guessed.
        const worker = one.worker_id
          ? names.ofWorker(one.worker_id)
          : C.unknown;
        return (
          <Typography
            key={`${one.name}-${index}`}
            data-testid="placed-check"
            data-step={one.step ?? ""}
            sx={{ typography: "s3", color: "text.secondary" }}
          >
            {C.checkStatus[one.status]} · {one.name} ·{" "}
            {one.untied
              ? C.untied[one.untied](one.tool)
              : C.placedOn(worker, one.tool)}
          </Typography>
        );
      })}
    </Stack>
  );
}
PlacedChecks.propTypes = {
  placements: PropTypes.arrayOf(PropTypes.object).isRequired,
  names: PropTypes.object.isRequired,
};
