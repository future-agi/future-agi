import React from "react";
import { Box, Stack, Tooltip, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";

const SEVERITY_CONFIG = {
  critical: {
    label: "Critical",
    dotColor: "#DB2F2D",
    darkDotColor: "#E87876",
    textColor: "#DB2F2D",
    darkTextColor: "#E87876",
  },
  high: {
    label: "High",
    dotColor: "#E9690C",
    darkDotColor: "#F49A54",
    textColor: "#E9690C",
    darkTextColor: "#F49A54",
  },
  medium: {
    label: "Medium",
    dotColor: "#B8AC47",
    darkDotColor: "#F5E65F",
    textColor: "#8C7A00",
    darkTextColor: "#F5E65F",
  },
  low: {
    label: "Low",
    dotColor: "#938FA3",
    darkDotColor: "#71717a",
    textColor: "#605C70",
    darkTextColor: "#71717a",
  },
};

export default function ErrorSeverityBadge({
  severity,
  assessmentStatus,
  source,
  reason,
}) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
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
            width: 7,
            height: 7,
            borderRadius: "50%",
            flexShrink: 0,
            bgcolor: isDark ? cfg.darkDotColor : cfg.dotColor,
            boxShadow: `0 0 0 2px ${isDark ? cfg.darkDotColor : cfg.dotColor}30`,
          }}
        />
        <Typography
          sx={{
            fontSize: "11px",
            fontWeight: 500,
            color: isDark ? cfg.darkTextColor : cfg.textColor,
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
