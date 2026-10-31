import React from "react";
import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

// Visual primitives shared by the workbench panels. They follow the
// Environments page: quiet pill chips for metadata, a dot pill for state, and
// the flush underline tab rail used across the product's tab bars.

export const QUERY_TABS_SX = {
  minHeight: 40,
  "& .MuiTab-root": {
    minHeight: 40,
    minWidth: 0,
    px: 1.5,
    typography: "s1",
    fontWeight: "fontWeightSemiBold",
    textTransform: "none",
    "&:not(:last-of-type)": { mr: 0 },
  },
  "& .MuiTab-root:not(.Mui-selected)": { color: "text.subtitle" },
  "& .MuiTab-root.Mui-selected": { color: "text.primary" },
  "& .MuiTabs-indicator": { height: 2, bgcolor: "text.primary" },
};

// Secondary actions: outlined, small, s2 bold, as on the Environments page.
export const ACTION_BUTTON_PROPS = { variant: "outlined", size: "small" };
export const CONTROL_HEIGHT = 32;
export const ACTION_BUTTON_SX = {
  height: CONTROL_HEIGHT,
  typography: "s2",
  fontWeight: "fontWeightSemiBold",
  whiteSpace: "nowrap",
};
export const PRIMARY_BUTTON_SX = {
  height: CONTROL_HEIGHT,
  typography: "s2",
  fontWeight: "fontWeightBold",
  whiteSpace: "nowrap",
};

// warning.dark is too light for 11px text on white; read warnings from the
// darker step in light mode and the light step in dark mode.
export const warningText = (t) =>
  t.palette.mode === "dark"
    ? t.palette.warning.light
    : t.palette.warning.darker;

export function MetaChip({
  icon,
  children,
  tone = "default",
  mono = false,
  bordered = true,
  title,
  sx,
}) {
  const color =
    tone === "warning"
      ? warningText
      : tone === "info"
        ? "info.dark"
        : "text.secondary";
  return (
    <Box
      component="span"
      title={title}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        flexShrink: 0,
        maxWidth: "100%",
        px: 0.75,
        py: 0.25,
        borderRadius: 0.75,
        border: "1px solid",
        borderColor: bordered ? "divider" : "transparent",
        bgcolor: (t) =>
          tone === "warning"
            ? alpha(t.palette.warning.main, 0.08)
            : tone === "info"
              ? alpha(t.palette.info.main, 0.08)
              : "background.neutral",
        color,
        typography: "s3",
        fontWeight: "fontWeightMedium",
        fontFamily: mono ? "monospace" : undefined,
        lineHeight: "16px",
        whiteSpace: "nowrap",
        ...sx,
      }}
    >
      {icon && <Iconify icon={icon} width={12} sx={{ flexShrink: 0 }} />}
      <Box
        component="span"
        sx={{ overflow: "hidden", textOverflow: "ellipsis" }}
      >
        {children}
      </Box>
    </Box>
  );
}
MetaChip.propTypes = {
  icon: PropTypes.string,
  children: PropTypes.node,
  tone: PropTypes.oneOf(["default", "warning", "info"]),
  mono: PropTypes.bool,
  bordered: PropTypes.bool,
  title: PropTypes.string,
  sx: PropTypes.object,
};

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
