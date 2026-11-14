import React from "react";
import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { META_TEXT_SX } from "./queryUiConfig";

// Visual primitives shared by the workbench panels. They follow the platform:
// read-only metadata is plain secondary text joined with " · " (never chips,
// which read as clickable), tab counts are a bare number after the label, and
// the run state is a tinted dot pill like the Environments StatusPill.

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
export function TabLabel({ label, count, hasMore = false }) {
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
          {hasMore ? "+" : ""}
        </Box>
      )}
    </Stack>
  );
}
TabLabel.propTypes = {
  label: PropTypes.string,
  count: PropTypes.number,
  hasMore: PropTypes.bool,
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
// same status as before, with its original accessible text.
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
        }}
        {...other}
      >
        {children}
      </Typography>
    </Stack>
  );
}
StatePill.propTypes = { state: PropTypes.string, children: PropTypes.node };
