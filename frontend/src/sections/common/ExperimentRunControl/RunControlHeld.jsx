// One run's own Stop and Resume while the server does not pause or resume
// single runs (run control off): they stay where they were, disabled, and say
// why. Nothing here sends a request.
import React, { useId } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { visuallyHidden } from "src/components/NotInRelease/notInRelease";

/** The reason under a disabled menu item's label, readable without hover. */
export function HeldReason({ text }) {
  return (
    <Typography
      component="span"
      typography="s3"
      color="text.secondary"
      data-run-control-held=""
      sx={{ display: "block", ml: 1, maxWidth: 220, whiteSpace: "normal" }}
    >
      {text}
    </Typography>
  );
}

HeldReason.propTypes = { text: PropTypes.string.isRequired };

/**
 * A disabled small icon action (a status chip's hover Stop or Resume) with
 * its reason in the tooltip and as its accessible description.
 */
export function HeldRunAction({ label, icon, color, reason }) {
  const descriptionId = useId();
  return (
    <CustomTooltip show title={reason} placement="top" size="small" arrow>
      <Box component="span" sx={{ display: "inline-flex" }}>
        <IconButton
          size="small"
          disabled
          aria-label={label}
          aria-describedby={descriptionId}
          sx={{
            p: 0.25,
            borderRadius: 0.5,
            border: "1px solid",
            borderColor: "divider",
            color: color ?? "text.secondary",
          }}
        >
          <Iconify icon={icon} width={14} />
        </IconButton>
        <Box component="span" id={descriptionId} sx={visuallyHidden}>
          {reason}
        </Box>
      </Box>
    </CustomTooltip>
  );
}

HeldRunAction.propTypes = {
  label: PropTypes.string.isRequired,
  icon: PropTypes.string.isRequired,
  color: PropTypes.string,
  reason: PropTypes.string.isRequired,
};
