import React from "react";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import PropTypes from "prop-types";

// Severity reads as a status role: the dot carries the hue, the word stays
// neutral except for critical.
const SEVERITY_CONFIG = {
  critical: { label: "Critical", role: "danger", text: "status.danger.fg" },
  high: { label: "High", role: "warning", text: "text.primary" },
  medium: { label: "Medium", role: "info", text: "text.primary" },
  low: { label: "Low", role: "neutral", text: "text.secondary" },
};

export default function ErrorSeverityBadge({
  severity,
  assessmentStatus,
  source,
  reason,
}) {
  const cfg = SEVERITY_CONFIG[severity] ?? SEVERITY_CONFIG.low;
  const fallback = source === "default" && assessmentStatus !== "completed";
  const label = fallback
    ? assessmentStatus === "pending"
      ? "Pending"
      : "Unassessed"
    : cfg.label;
  const explanation = fallback
    ? `Severity has not been established (${assessmentStatus || "unassessed"}). Medium is only a compatibility default. ${reason || ""}`
    : `${reason || cfg.label}${assessmentStatus === "pending" ? " — reassessment pending" : ""}`;

  return (
    <Tooltip title={explanation}>
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
          {label}
        </Typography>
      </Stack>
    </Tooltip>
  );
}

ErrorSeverityBadge.propTypes = {
  severity: PropTypes.oneOf(["critical", "high", "medium", "low"]),
  assessmentStatus: PropTypes.string,
  source: PropTypes.string,
  reason: PropTypes.string,
};
