// Small shared pieces of the connect UI (R4 implementation): the S2 `Simulated` tag,
// a reason tooltip around a disabled control, a copyable command line and
// a status dot. Native look: theme tokens, s1–s3, CustomTooltip.
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import CopyButton from "src/sections/common/ux/CopyButton";
import { VOCAB } from "src/sections/common/vocab";
import { standInTip } from "./connectCopy";

export const MONO = {
  fontFamily:
    'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
};

/**
 * S2: the small `Simulated` tag next to a stand-in button, with the one
 * tooltip wording (connectCopy.standInTip, L7c g1 the capability contract c).
 */
export function SimulatedTag({ event, title, sx }) {
  const tip = title ?? standInTip(event);
  return (
    <CustomTooltip show size="small" placement="top" title={tip}>
      <Box
        component="span"
        data-testid="simulated-tag"
        aria-label={`${VOCAB.SIMULATED}: ${tip}`}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          flexShrink: 0,
          px: 0.5,
          height: 18,
          borderRadius: 0.5,
          border: "1px dashed",
          borderColor: "divider",
          typography: "s3",
          color: "text.secondary",
          cursor: "help",
          ...sx,
        }}
      >
        {VOCAB.SIMULATED}
      </Box>
    </CustomTooltip>
  );
}
SimulatedTag.propTypes = {
  event: PropTypes.string,
  title: PropTypes.string,
  sx: PropTypes.object,
};

/** Wrap a (possibly disabled) control so its reason shows on hover. */
export function ReasonTip({ reason, title, children, placement = "top" }) {
  const text = reason || title;
  if (!text) return children;
  return (
    <CustomTooltip show size="small" placement={placement} title={text}>
      <Box component="span" sx={{ display: "inline-flex" }}>
        {children}
      </Box>
    </CustomTooltip>
  );
}
ReasonTip.propTypes = {
  reason: PropTypes.string,
  title: PropTypes.node,
  children: PropTypes.node,
  placement: PropTypes.string,
};

/** A command (one or more lines) with its Copy button; copies `command`, shows `display`. */
export function CommandLine({ command, display, label = "Copy command", sx }) {
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      gap={0.5}
      sx={{
        minWidth: 0,
        pl: 1,
        pr: 0.25,
        py: 0.25,
        borderRadius: 0.75,
        bgcolor: "background.neutral",
        border: "1px solid",
        borderColor: "divider",
        ...sx,
      }}
    >
      <Typography
        component="code"
        typography="s2"
        sx={{
          ...MONO,
          flex: 1,
          minWidth: 0,
          py: 0.5,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          color: "text.primary",
        }}
      >
        {display ?? command}
      </Typography>
      <CopyButton value={command} tooltip={label} />
    </Stack>
  );
}
CommandLine.propTypes = {
  command: PropTypes.string.isRequired,
  display: PropTypes.string,
  label: PropTypes.string,
  sx: PropTypes.object,
};

/** A status dot: success / grey (pulsing while waiting) / warning / error. */
export function Dot({ tone = "grey", pulse = false, size = 10 }) {
  const color =
    tone === "success"
      ? "success.main"
      : tone === "warning"
        ? "warning.main"
        : tone === "error"
          ? "error.main"
          : tone === "info"
            ? "info.main"
            : "text.disabled";
  return (
    <Box
      aria-hidden
      sx={{
        width: size,
        height: size,
        borderRadius: "50%",
        bgcolor: color,
        flexShrink: 0,
        ...(pulse
          ? {
              "@keyframes faConnectPulse": {
                "0%": { opacity: 1, transform: "scale(1)" },
                "50%": { opacity: 0.35, transform: "scale(0.75)" },
                "100%": { opacity: 1, transform: "scale(1)" },
              },
              animation: "faConnectPulse 1.6s ease-in-out infinite",
              "@media (prefers-reduced-motion: reduce)": { animation: "none" },
            }
          : {}),
      }}
    />
  );
}
Dot.propTypes = {
  tone: PropTypes.string,
  pulse: PropTypes.bool,
  size: PropTypes.number,
};
