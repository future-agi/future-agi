import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";

import Iconify from "../iconify";
import { UNAVAILABLE, LOAD_FAILED } from "./failureVariants";

const COPY = {
  [UNAVAILABLE]: {
    title: "Recording unavailable",
    detail: "This call's audio can't be reached.",
  },
  [LOAD_FAILED]: {
    title: "Audio failed to load",
    detail: "The recording didn't finish loading.",
  },
};

/**
 * Shown in place of the waveform when the tracks cannot be played. Fills the
 * footprint the waveform would have occupied so a failure does not reflow the
 * drawer, and stays compact enough for the 50px-per-track call sites as well
 * as the 70px default.
 */
const RecordingFailure = ({ variant, onRetry }) => {
  const { title, detail } = COPY[variant];

  return (
    <Box
      role="status"
      sx={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 0.5,
        px: 2,
        textAlign: "center",
        bgcolor: "background.paper",
        zIndex: 10,
      }}
    >
      <Iconify
        icon="solar:danger-triangle-linear"
        width={20}
        sx={{ color: "warning.main", mb: 0.25 }}
      />
      <Typography fontWeight={600} sx={{ fontSize: "13px" }}>
        {title}
      </Typography>
      <Typography color="text.secondary" sx={{ fontSize: "11px" }}>
        {detail}
      </Typography>
      {variant === LOAD_FAILED && (
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={onRetry}
          sx={(t) => ({
            mt: 0.75,
            minWidth: 0,
            px: 1.25,
            py: 0.25,
            fontSize: "11px",
            borderColor: alpha(t.palette.warning.main, 0.5),
            color: "text.primary",
            "&:hover": {
              borderColor: "warning.main",
              bgcolor: alpha(t.palette.warning.main, 0.08),
            },
          })}
        >
          Retry
        </Button>
      )}
    </Box>
  );
};

RecordingFailure.propTypes = {
  variant: PropTypes.oneOf([UNAVAILABLE, LOAD_FAILED]).isRequired,
  onRetry: PropTypes.func,
};

export default RecordingFailure;
