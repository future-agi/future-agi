import PropTypes from "prop-types";
import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import { rateTone } from "./format";
import { isRunning } from "./runModel";

export function PassRateCell({ run }) {
  const rate = isRunning(run) ? null : run.passRate;
  const measured = (run.passed ?? 0) + (run.failed ?? 0) + (run.skipped ?? 0);
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
          width: 76,
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
      </Typography>
    </Stack>
  );
}

PassRateCell.propTypes = { run: PropTypes.object.isRequired };
