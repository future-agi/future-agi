import { Box, Chip, Skeleton, Tooltip } from "@mui/material";
import { alpha } from "@mui/material/styles";
import PropTypes from "prop-types";
import React from "react";
import { EVAL_STATUS, getEvalStatusLabel } from "src/utils/evalStatus";

// Single source of truth for how a not-yet-scored eval renders across every
// surface (spans/traces/session/voice lists + their detail drawers):
//   - running  -> a skeleton that fills the whole cell, mirroring how a
//                 completed eval result covers the entire cell
//   - pending  -> "Queued" blue pill (design system)
//   - skipped  -> muted chip + reason tooltip
//   - errored  -> muted red "Error" chip
// Returns null for a terminal score state (the caller renders the score).

// Centered, full-height wrapper so the pill/chip states sit in the cell the
// same way a completed score does.
const CellWrap = ({ children }) => (
  <Box
    sx={{
      width: "100%",
      height: "100%",
      minHeight: 20,
      display: "flex",
      alignItems: "center",
      px: 1.5,
    }}
  >
    {children}
  </Box>
);
CellWrap.propTypes = { children: PropTypes.node };

const EvalStatusIndicator = ({ status, skippedReason }) => {
  if (status === EVAL_STATUS.RUNNING) {
    // Full-cell loading skeleton — same footprint as a rendered eval result.
    return (
      <Skeleton
        variant="rectangular"
        animation="wave"
        sx={{
          width: "100%",
          height: "100%",
          minHeight: 20,
          borderRadius: 0,
          transform: "none", // fill the cell instead of MUI's default y-scale
        }}
      />
    );
  }

  if (status === EVAL_STATUS.PENDING) {
    return (
      <CellWrap>
        <Box
          component="span"
          sx={{
            display: "inline-flex",
            alignItems: "center",
            px: 1.25,
            py: 0.5,
            borderRadius: "8px",
            // Theme-aware "Queued" pill: keep the approved light-mode blues,
            // fall back to a translucent info pill on dark so it doesn't render
            // a bright light-blue chip on a dark surface.
            bgcolor: (theme) =>
              theme.palette.mode === "dark"
                ? alpha(theme.palette.info.main, 0.24)
                : "#E9F0FD",
            color: (theme) =>
              theme.palette.mode === "dark"
                ? theme.palette.info.light
                : "#3A66C2",
            fontSize: 13,
            fontWeight: 500,
            lineHeight: 1.4,
            whiteSpace: "nowrap",
          }}
        >
          {getEvalStatusLabel(status)}
        </Box>
      </CellWrap>
    );
  }

  if (status === EVAL_STATUS.SKIPPED) {
    const chip = (
      <Chip
        size="small"
        label={getEvalStatusLabel(status)}
        sx={{
          height: 20,
          fontSize: 11,
          color: "text.disabled",
          bgcolor: "action.hover",
        }}
      />
    );
    return (
      <CellWrap>
        {skippedReason ? <Tooltip title={skippedReason}>{chip}</Tooltip> : chip}
      </CellWrap>
    );
  }

  if (status === EVAL_STATUS.ERRORED || status === "error") {
    return (
      <CellWrap>
        <Chip
          size="small"
          label="Error"
          sx={{
            height: 20,
            fontSize: 11,
            color: "error.main",
            bgcolor: (theme) => alpha(theme.palette.error.main, 0.1),
          }}
        />
      </CellWrap>
    );
  }

  return null;
};

EvalStatusIndicator.propTypes = {
  status: PropTypes.string,
  skippedReason: PropTypes.string,
};

export default EvalStatusIndicator;
