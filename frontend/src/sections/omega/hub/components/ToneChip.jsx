/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

import { TONE_ROLES } from "../lib/status";

// DS v0.2 tokens.json color.status.*.fg; lib/status owns the role mapping.
export function toneColor(theme, tone) {
  return theme.palette.status[TONE_ROLES[tone] || "neutral"].fg;
}

export const toneSx = (tone) => ({ color: (theme) => toneColor(theme, tone) });

// A small outlined label in one tone.
export default function ToneChip({
  label,
  tone = "neutral",
  icon,
  testId,
  ...rest
}) {
  return (
    <Box
      component="span"
      data-testid={testId}
      {...rest}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        px: 0.875,
        py: 0.125,
        borderRadius: 0.75,
        border: "1px solid",
        borderColor: (theme) => toneColor(theme, tone),
        color: (theme) => toneColor(theme, tone),
        typography: "s3",
        fontWeight: 600,
        lineHeight: "18px",
        whiteSpace: "nowrap",
        maxWidth: "100%",
        overflow: "hidden",
        textOverflow: "ellipsis",
      }}
    >
      {icon}
      {label}
    </Box>
  );
}

ToneChip.propTypes = {
  label: PropTypes.node.isRequired,
  tone: PropTypes.oneOf(Object.keys(TONE_ROLES)),
  icon: PropTypes.node,
  testId: PropTypes.string,
};
