import PropTypes from "prop-types";
import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import { paths } from "src/routes/paths";
import { rateTone } from "./format";

// TestExecution.ExecutionStatus: pending, running, evaluating, cancelling,
// completed, failed, cancelled.
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

// Calls left out of the rate (errored, or no score), so a rate over few measured calls is never read alone.
export const unratedOf = (run) => {
  const parts = [
    run.errored ? `${run.errored} errored` : null,
    run.noScore ? `${run.noScore} no score` : null,
  ].filter(Boolean);
  return parts.join(", ");
};

export function PassRateCell({ run }) {
  const rate = isRunning(run) ? null : run.passRate;
  const measured = (run.passed ?? 0) + (run.failed ?? 0);
  const unrated = isRunning(run) ? "" : unratedOf(run);
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ width: "100%" }}
      data-testid={`pass-rate-${run.testExecutionId}`}
    >
      <Box
        sx={{
          flex: 1,
          height: 6,
          borderRadius: 3,
          bgcolor: "background.neutral",
          overflow: "hidden",
        }}
      >
        <Box
          sx={{
            width: `${rate || 0}%`,
            height: "100%",
            bgcolor:
              rate == null
                ? "transparent"
                : rate < 50
                  ? "status.danger.solid"
                  : "status.neutral.solid",
          }}
        />
      </Box>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 500,
          fontVariantNumeric: "tabular-nums",
          minWidth: 76,
          whiteSpace: "nowrap",
          textAlign: "right",
          color: rate == null ? "text.disabled" : rateTone(rate),
        }}
      >
        {rate == null ? "—" : `${rate}%`}
        {rate != null && (
          <Box
            component="span"
            sx={{ color: "text.subtitle", fontWeight: 400 }}
          >
            {` ${run.passed}/${measured}`}
          </Box>
        )}
        {unrated && (
          <Box
            component="span"
            sx={{ color: "text.subtitle", fontWeight: 400 }}
          >
            {` · ${unrated}`}
          </Box>
        )}
      </Typography>
    </Stack>
  );
}

PassRateCell.propTypes = { run: PropTypes.object.isRequired };
