import React from "react";
import PropTypes from "prop-types";
import { Box, Tooltip, Typography, alpha } from "@mui/material";
import Iconify from "src/components/iconify";

// Shared "Add feedback" chip. Visually mirrors the "Fix with Falcon" chip
// in EvalsTabView.jsx:343-379 so the two affordances stay in lockstep when
// they appear together. Dropped into:
//   - components/traceDetail/EvalsTabView.jsx (next to "Fix with Falcon")
//   - sections/projects/TracesDrawer/SessionEvalsList.jsx (trailing column)
//
// Props
//   onClick               — fires only when not disabled
//   disabled              — visual + behavior; when true the chip becomes
//                           unclickable (pointer-events: none) and dims
//   tooltipWhenDisabled   — title for the wrapping Tooltip on disabled state.
//                           Skipped if not provided.
//   sx                    — caller-supplied style overrides merged on top
const chipSx = {
  display: "inline-flex",
  alignItems: "center",
  gap: 0.5,
  px: 0.75,
  py: 0.25,
  alignSelf: "flex-start",
  // Keep the chip on one line — when dropped into a tight row (EvalsTabView
  // action column), the parent's flex shrink would otherwise squeeze the
  // chip narrow enough that the label wraps to 2 lines, doubling row height.
  flexShrink: 0,
  whiteSpace: "nowrap",
  border: "1px solid",
  borderColor: (theme) => alpha(theme.palette.primary.main, 0.4),
  borderRadius: "4px",
  cursor: "pointer",
  bgcolor: (theme) => alpha(theme.palette.primary.main, 0.06),
  "&:hover": {
    bgcolor: (theme) => alpha(theme.palette.primary.main, 0.12),
  },
};

const disabledSx = {
  pointerEvents: "none",
  opacity: 0.4,
};

const AddFeedbackChip = ({
  onClick,
  disabled = false,
  tooltipWhenDisabled,
  sx,
}) => {
  const handleClick = (e) => {
    e.stopPropagation();
    if (disabled) return;
    onClick?.(e);
  };

  const chip = (
    <Box
      onClick={handleClick}
      sx={{ ...chipSx, ...(disabled ? disabledSx : {}), ...sx }}
      data-testid="add-feedback-chip"
    >
      <Iconify
        icon="mdi:message-plus-outline"
        width={12}
        color="primary.main"
      />
      <Typography
        sx={{ fontSize: 10, fontWeight: 600, color: "primary.main" }}
      >
        Add feedback
      </Typography>
    </Box>
  );

  // MUI's Tooltip refuses to attach handlers to a disabled child — wrap in
  // a span when disabled so the title still renders.
  if (disabled && tooltipWhenDisabled) {
    return (
      <Tooltip title={tooltipWhenDisabled} arrow>
        <span>{chip}</span>
      </Tooltip>
    );
  }
  return chip;
};

AddFeedbackChip.propTypes = {
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
  tooltipWhenDisabled: PropTypes.string,
  sx: PropTypes.oneOfType([PropTypes.object, PropTypes.array]),
};

export default AddFeedbackChip;
