// Small shared pieces of the R4 run-source drawer (lane Ra): the quiet
// neutral tag and the numbered section header. Native look: MUI
// tokens, s1–s3, Iconify mdi:, CustomTooltip.
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

/**
 * A quiet neutral tag (options-truth L1 C11). The old status words
 * (`today`, `preview`, `coming_soon`) render nothing: every option works.
 * A caller that still has a technical fact to show passes it as `label`
 * (e.g. `OTel`), with an optional `reason` tooltip.
 */
export function StatusTag({ status, reason, label, sx }) {
  const text = String(label ?? "").trim();
  if (!text) return null;
  const tag = (
    <Stack
      component="span"
      direction="row"
      alignItems="center"
      gap={0.5}
      data-status-tag={status ?? "label"}
      sx={{ flexShrink: 0, ...sx }}
    >
      <Typography
        component="span"
        typography="s3"
        color="text.secondary"
        noWrap
      >
        {text}
      </Typography>
    </Stack>
  );
  if (!reason) return tag;
  return (
    <CustomTooltip show title={reason} size="small" placement="top" arrow>
      <span style={{ display: "inline-flex" }}>{tag}</span>
    </CustomTooltip>
  );
}
StatusTag.propTypes = {
  status: PropTypes.string,
  reason: PropTypes.string,
  label: PropTypes.string,
  sx: PropTypes.object,
};

/** `1  Run it` — the numbered heading of a Level 3 section. */
export function SectionHeading({ n, title, right, sx }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{ minHeight: 28, ...sx }}
    >
      {n == null ? null : (
        <Box
          sx={{
            width: 20,
            height: 20,
            borderRadius: "50%",
            border: "1px solid",
            borderColor: "divider",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            typography: "s3",
            color: "text.secondary",
            flexShrink: 0,
          }}
        >
          {n}
        </Box>
      )}
      <Typography typography="s1" fontWeight="fontWeightSemiBold" noWrap>
        {title}
      </Typography>
      <Box sx={{ flex: 1 }} />
      {right}
    </Stack>
  );
}
SectionHeading.propTypes = {
  /** IT-R1 G1 P1: optional; sections are unnumbered (steps carry numbers). */
  n: PropTypes.number,
  title: PropTypes.string.isRequired,
  right: PropTypes.node,
  sx: PropTypes.object,
};

/** A small dot with a tooltip (declared / seen in a run). */
export function Dot({ color, title }) {
  return (
    <CustomTooltip show title={title} size="small" placement="top" arrow>
      <Box
        component="span"
        aria-label={title}
        sx={{
          display: "inline-block",
          width: 7,
          height: 7,
          borderRadius: "50%",
          bgcolor: color,
          flexShrink: 0,
        }}
      />
    </CustomTooltip>
  );
}
Dot.propTypes = { color: PropTypes.string, title: PropTypes.string };

/** `mdi:alert` + one amber line (warnings that never block). */
export function AmberLine({ children, icon = "mdi:alert-outline", sx }) {
  return (
    <Stack
      direction="row"
      gap={0.75}
      alignItems="flex-start"
      sx={{
        px: 1,
        py: 0.75,
        borderRadius: 0.5,
        bgcolor: (t) =>
          t.palette.mode === "dark"
            ? "rgba(255, 171, 0, 0.12)"
            : "rgba(255, 171, 0, 0.12)",
        ...sx,
      }}
    >
      <Iconify
        icon={icon}
        width={16}
        sx={{ color: "warning.dark", flexShrink: 0, mt: "1px" }}
      />
      <Typography typography="s2" color="text.primary" sx={{ minWidth: 0 }}>
        {children}
      </Typography>
    </Stack>
  );
}
AmberLine.propTypes = {
  children: PropTypes.node,
  icon: PropTypes.string,
  sx: PropTypes.object,
};
