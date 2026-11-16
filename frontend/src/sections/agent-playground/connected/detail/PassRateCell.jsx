import PropTypes from "prop-types";
import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import { rateTone } from "./format";
import { isRunning, unratedOf } from "./runFacts";

export default function PassRateCell({ run }) {
  const rate = isRunning(run) ? null : run.passRate;
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
            {` ${run.passed}/${run.scored}`}
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
