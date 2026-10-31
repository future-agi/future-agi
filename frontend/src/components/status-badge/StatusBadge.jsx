import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { keyframes, useTheme } from "@mui/material/styles";

import { runStatusOf, statusColors, STATUS_ROLES } from "src/utils/runStatus";

// ----------------------------------------------------------------------
// Future AGI Design System · Badge and status. Two ways to show state:
// a tinted badge where status is the point of the cell, or a dot + label
// where the row has lots going on and for every neutral state. Never
// outlined in colour, never colour alone. Colours come from theme.palette.status via the one status map.
// ----------------------------------------------------------------------

const pulse = keyframes`
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
`;

function Dot({ colors, shape, size }) {
  const hollow = shape === "hollow";
  return (
    <Box
      component="span"
      sx={{
        width: size,
        height: size,
        borderRadius: "50%",
        flexShrink: 0,
        bgcolor: hollow ? "transparent" : colors.solid,
        border: hollow ? `1.5px solid ${colors.solid}` : 0,
        animation:
          shape === "live" ? `${pulse} 1.6s ease-in-out infinite` : "none",
        "@media (prefers-reduced-motion: reduce)": { animation: "none" },
      }}
    />
  );
}
Dot.propTypes = {
  colors: PropTypes.object,
  shape: PropTypes.string,
  size: PropTypes.number,
};

export default function StatusBadge({
  status,
  label,
  role,
  variant,
  size = "md",
  icon,
  dot = true,
  sx,
  ...other
}) {
  const theme = useTheme();
  const meta = runStatusOf(status, label);
  const colors = statusColors(theme, role || meta.role);
  // Neutral states (not run, queued, draft…) say nothing has happened, so
  // they take the quieter dot + label unless a caller asks for the badge.
  const shown =
    variant || ((role || meta.role) === "neutral" ? "dot" : "badge");

  if (shown === "dot") {
    return (
      <Box
        component="span"
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.75,
          minWidth: 0,
          ...sx,
        }}
        {...other}
      >
        <Dot colors={colors} shape={meta.shape} size={8} />
        <Typography
          component="span"
          noWrap
          // Same size as table values (12px) so a status sits level with
          // the cells around it.
          sx={{ fontSize: 12, lineHeight: "16px", color: "text.secondary" }}
        >
          {meta.label}
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.625,
        height: size === "sm" ? 18 : 20,
        px: 0.75,
        borderRadius: "4px",
        bgcolor: colors.bg,
        color: colors.fg,
        flexShrink: 0,
        maxWidth: "100%",
        ...sx,
      }}
      {...other}
    >
      {icon || (dot && <Dot colors={colors} shape={meta.shape} size={6} />)}
      <Typography
        component="span"
        noWrap
        sx={{
          fontSize: 12,
          lineHeight: "16px",
          fontWeight: 500,
          color: "inherit",
        }}
      >
        {meta.label}
      </Typography>
    </Box>
  );
}

StatusBadge.propTypes = {
  status: PropTypes.oneOfType([
    PropTypes.string,
    PropTypes.bool,
    PropTypes.number,
  ]),
  label: PropTypes.node,
  role: PropTypes.oneOf(STATUS_ROLES),
  variant: PropTypes.oneOf(["badge", "dot"]),
  size: PropTypes.oneOf(["md", "sm"]),
  icon: PropTypes.node,
  dot: PropTypes.bool,
  sx: PropTypes.object,
};
