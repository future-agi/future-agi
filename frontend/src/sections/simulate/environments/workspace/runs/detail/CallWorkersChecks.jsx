import PropTypes from "prop-types";
import { Link, Stack, Typography } from "@mui/material";
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

/** Each failed or unsure check, with its worker when it is that worker's own, on its hand-off or why it is on none. */
export function PlacedChecks({ placements, names, stepOf, onPick }) {
  return (
    <Stack spacing={0.5} data-testid="placed-checks">
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {C.placedChecks}
      </Typography>
      {placements.map((one, index) => (
        <Typography
          key={`${one.name}-${index}`}
          data-testid="placed-check"
          data-step={one.step ?? ""}
          sx={{ typography: "s3", color: "text.secondary" }}
        >
          {C.checkStatus[one.status]} · {one.name}
          {/* A worker's own check names its worker; any other names none rather than a guessed one. */}
          {one.worker_id ? ` · ${names.ofWorker(one.worker_id)}` : ""}
          {one.untied ? (
            ` · ${C.untied[one.untied]}`
          ) : stepOf?.(one.step) != null ? (
            <>
              {" · "}
              {/* The step it sits on, by the sequence's own number; it opens that step. */}
              <Link
                component="button"
                type="button"
                underline="hover"
                onClick={() => onPick(stepOf(one.step))}
                sx={{ typography: "s3", verticalAlign: "baseline" }}
              >
                {C.onStep(stepOf(one.step))}
              </Link>
            </>
          ) : (
            ` · ${C.onHandOff}`
          )}
        </Typography>
      ))}
    </Stack>
  );
}
PlacedChecks.propTypes = {
  placements: PropTypes.arrayOf(PropTypes.object).isRequired,
  names: PropTypes.object.isRequired,
  // A placement's API step key → the sequence's step number, or null.
  stepOf: PropTypes.func,
  onPick: PropTypes.func,
};
