import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";

// Severity reads as a status role: the dot carries the hue, the word stays
// neutral except for critical.
const SEVERITY_CONFIG = {
  critical: { label: "Critical", role: "danger", text: "status.danger.fg" },
  high: { label: "High", role: "warning", text: "text.primary" },
  medium: { label: "Medium", role: "info", text: "text.primary" },
  low: { label: "Low", role: "neutral", text: "text.secondary" },
};

export default function ErrorSeverityBadge({ severity }) {
  const cfg = SEVERITY_CONFIG[severity] ?? SEVERITY_CONFIG.low;

  return (
    <Stack direction="row" alignItems="center" gap={0.75}>
      <Box
        sx={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          flexShrink: 0,
          bgcolor: `status.${cfg.role}.solid`,
        }}
      />
      <Typography
        sx={{
          fontSize: "12px",
          fontWeight: 500,
          color: cfg.text,
          lineHeight: 1,
          letterSpacing: "0.02em",
          textTransform: "capitalize",
        }}
      >
        {cfg.label}
      </Typography>
    </Stack>
  );
}

ErrorSeverityBadge.propTypes = {
  severity: PropTypes.oneOf(["critical", "high", "medium", "low"]),
};
