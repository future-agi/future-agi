import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { keyframes, useTheme } from "@mui/material/styles";
import { runStatusOf, statusColors, STATUS_ROLES } from "src/utils/runStatus";
import { dsRadius, dsTypography } from "src/theme/ds-tokens";

// Adapted from Vel 05a5f9f77a: one role map, never color alone.
const pulse = keyframes`0%, 100% { opacity: 1; } 50% { opacity: .35; }`;
function Dot({ colors, shape, size, disabled }) {
  return (
    <Box
      component="span"
      aria-hidden="true"
      sx={{
        width: size,
        height: size,
        flexShrink: 0,
        borderRadius: "50%",
        bgcolor: shape === "hollow" ? "transparent" : colors.solid,
        border: shape === "hollow" ? `1px solid ${colors.solid}` : 0,
        animation:
          !disabled && shape === "live"
            ? `${pulse} 1.6s ease-in-out infinite`
            : "none",
        "@media (prefers-reduced-motion: reduce)": { animation: "none" },
      }}
    />
  );
}
Dot.propTypes = {
  colors: PropTypes.object,
  shape: PropTypes.string,
  size: PropTypes.number,
  disabled: PropTypes.bool,
};

export default function StatusBadge({
  status,
  label,
  role,
  variant,
  size = "md",
  icon,
  dot = true,
  disabled = false,
  sx,
  ...other
}) {
  const theme = useTheme();
  const meta = runStatusOf(status, label);
  const colors = statusColors(theme, role || meta.role);
  const shown =
    variant || ((role || meta.role) === "neutral" ? "dot" : "badge");
  const dotOnly = shown === "dot";
  const visibleLabel =
    meta.label === "" || meta.label == null ? "Unknown" : meta.label;
  return (
    <Box
      component="span"
      aria-disabled={disabled || undefined}
      {...other}
      sx={[
        {
          display: "inline-flex",
          alignItems: "center",
          gap: 0.75,
          minWidth: 0,
          maxWidth: "100%",
          height: size === "sm" ? 18 : 20,
          px: dotOnly ? 0 : 0.75,
          borderRadius: `${dsRadius.sm}px`,
          bgcolor: dotOnly ? "transparent" : colors.bg,
          color: dotOnly ? "text.secondary" : colors.fg,
          ...(disabled && {
            color: "text.disabled",
            bgcolor: dotOnly ? "transparent" : "action.disabledBackground",
          }),
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    >
      {icon ? (
        <Box
          component="span"
          sx={{
            display: "inline-flex",
            flexShrink: 0,
            color: disabled ? "text.disabled" : colors.solid,
          }}
        >
          {icon}
        </Box>
      ) : (
        dot && (
          <Dot
            colors={disabled ? { solid: theme.palette.text.disabled } : colors}
            shape={meta.shape}
            size={dotOnly ? 8 : 6}
            disabled={disabled}
          />
        )
      )}
      <Typography
        component="span"
        noWrap
        sx={{
          ...(dotOnly ? dsTypography.body_sm : dsTypography.label_sm),
          fontSize: 12,
          lineHeight: "16px",
          fontWeight: dotOnly ? 400 : 500,
          color: "inherit",
        }}
      >
        {visibleLabel}
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
  disabled: PropTypes.bool,
  sx: PropTypes.oneOfType([PropTypes.object, PropTypes.array, PropTypes.func]),
};
export function StatusDot(props) {
  return <StatusBadge {...props} variant="dot" />;
}
