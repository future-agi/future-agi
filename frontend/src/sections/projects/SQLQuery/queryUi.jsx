import React from "react";
import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

// Visual primitives shared by the workbench panels. They follow the platform:
// read-only metadata is plain secondary text joined with " · " (never chips,
// which read as clickable), tab counts are a bare number after the label, and
// the run state is a tinted dot pill like the Environments StatusPill.

export const QUERY_TABS_SX = {
  minHeight: 40,
  "& .MuiTab-root": {
    minHeight: 40,
    minWidth: 0,
    px: 1.25,
    typography: "s2",
    fontWeight: "fontWeightSemiBold",
    textTransform: "none",
    "&:not(:last-of-type)": { mr: 0 },
  },
  "& .MuiTab-root:not(.Mui-selected)": { color: "text.subtitle" },
  "& .MuiTab-root.Mui-selected": { color: "text.primary" },
  "& .MuiTabs-indicator": { height: 2, bgcolor: "text.primary" },
};

export const CONTROL_HEIGHT = 32;
// Primary action: contained brand primary, as on Environments and most pages.
export const PRIMARY_BUTTON_PROPS = {
  variant: "contained",
  color: "primary",
  size: "small",
};
export const PRIMARY_BUTTON_SX = {
  height: CONTROL_HEIGHT,
  typography: "s2",
  fontWeight: "fontWeightBold",
  whiteSpace: "nowrap",
};
// Secondary actions: the theme's neutral outlined button.
export const ACTION_BUTTON_PROPS = { variant: "outlined", size: "small" };
export const ACTION_BUTTON_SX = {
  height: CONTROL_HEIGHT,
  typography: "s2",
  fontWeight: "fontWeightSemiBold",
  whiteSpace: "nowrap",
};

// warning.dark is too light for small text on white; read warnings from the
// darker step in light mode and the light step in dark mode.
export const warningText = (t) =>
  t.palette.mode === "dark"
    ? t.palette.warning.light
    : t.palette.warning.darker;

export const META_TEXT_SX = {
  typography: "s3",
  color: "text.subtitle",
  fontVariantNumeric: "tabular-nums",
};

// "4 rows · 3 columns · 37 ms" — falsy parts are dropped.
export function MetaLine({ parts, icon, sx, ...other }) {
  const text = parts.filter(Boolean).join(" · ");
  if (!text) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{ minWidth: 0, ...sx }}
      {...other}
    >
      {icon && (
        <Iconify
          icon={icon}
          width={14}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
      )}
      <Typography component="span" sx={META_TEXT_SX}>
        {text}
      </Typography>
    </Stack>
  );
}
MetaLine.propTypes = {
  parts: PropTypes.arrayOf(PropTypes.node).isRequired,
  icon: PropTypes.string,
  sx: PropTypes.object,
};

// Label plus a bare count, shown only when greater than 0 (WorkspaceTabLabel).
export function TabLabel({ label, count }) {
  return (
    <Stack direction="row" alignItems="baseline" spacing={0.625}>
      <span>{label}</span>
      {count > 0 && (
        <Box
          component="span"
          aria-hidden
          sx={{ ...META_TEXT_SX, fontWeight: 500 }}
        >
          {count}
        </Box>
      )}
    </Stack>
  );
}
TabLabel.propTypes = { label: PropTypes.string, count: PropTypes.node };

export const visibilityLabel = (visibility) =>
  visibility === "project" ? "Shared with project" : "Private";
export const visibilityIcon = (visibility) =>
  visibility === "project"
    ? "solar:users-group-rounded-linear"
    : "solar:lock-keyhole-minimalistic-linear";

const STATE_COLOR = {
  succeeded: (t) => t.palette.success.main,
  failed: (t) => t.palette.error.main,
  running: (t) => t.palette.info.main,
  queued: (t) => t.palette.info.main,
  submitting: (t) => t.palette.info.main,
};
const idleColor = (t) => t.palette.text.disabled;

// Dot + label pill for the run state, tinted like the Environments StatusPill.
// The label text is passed in unchanged so screen readers and tests read the
// same status as before; only its casing is styled.
export function StatePill({ state, children, ...other }) {
  const color = STATE_COLOR[state] || idleColor;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      sx={{
        display: "inline-flex",
        flexShrink: 0,
        px: 0.875,
        py: 0.25,
        borderRadius: 999,
        border: "1px solid",
        borderColor: (t) => alpha(color(t), 0.35),
        bgcolor: (t) =>
          alpha(color(t), t.palette.mode === "dark" ? 0.14 : 0.09),
      }}
    >
      <Box
        sx={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          bgcolor: color,
          flexShrink: 0,
        }}
      />
      <Typography
        component="span"
        sx={{
          typography: "s3",
          fontWeight: "fontWeightSemiBold",
          color: "text.primary",
          display: "inline-block",
          "&::first-letter": { textTransform: "uppercase" },
        }}
        {...other}
      >
        {children}
      </Typography>
    </Stack>
  );
}
StatePill.propTypes = { state: PropTypes.string, children: PropTypes.node };
