import React from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, CircularProgress } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

/** D4 colours of a verdict: success when good, error when bad. */
export function verdictColors(status, theme) {
  const dark = theme.palette.mode === "dark";
  switch (status) {
    case "pass":
      return {
        bg: alpha(theme.palette.green[600], 0.12),
        fg: dark ? theme.palette.green[400] : theme.palette.green[700],
        dot: theme.palette.green[600],
      };
    case "fail":
      return {
        bg: alpha(theme.palette.red[600], 0.12),
        fg: dark ? theme.palette.red[400] : theme.palette.red[700],
        dot: theme.palette.red[600],
      };
    case "error":
      return {
        bg: "transparent",
        fg: dark ? theme.palette.red[400] : theme.palette.red[700],
        dot: theme.palette.red[600],
        border: alpha(theme.palette.red[600], 0.5),
      };
    default:
      return {
        bg: theme.palette.action.hover,
        fg: theme.palette.text.secondary,
        dot: theme.palette.text.disabled,
      };
  }
}

/**
 * One eval verdict as a chip: `Correctness · Fail`, `Groundedness · 0.41`.
 * Clicking it opens the reason inline (the caller renders EvalDetail).
 */
const EvalChip = ({ name, verdict, selected, onClick, tooltip }) => {
  const theme = useTheme();
  const c = verdictColors(verdict.status, theme);
  const running = verdict.status === "running";
  const chip = (
    <ButtonBase
      onClick={onClick}
      aria-pressed={selected}
      aria-label={name ? `${name} · ${verdict.display}` : verdict.display}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        height: 24,
        px: 1,
        borderRadius: 0.75,
        bgcolor: c.bg,
        color: c.fg,
        border: "1px solid",
        borderColor: selected ? c.dot : c.border ?? "transparent",
        typography: "s2",
        fontWeight: 500,
        whiteSpace: "nowrap",
        transition: "border-color 120ms",
        "&:hover": { borderColor: c.dot },
      }}
    >
      {running ? (
        <CircularProgress size={10} thickness={6} sx={{ color: c.fg }} />
      ) : verdict.status === "error" ? (
        <Iconify icon="mdi:alert-circle-outline" width={13} />
      ) : (
        <Box
          component="span"
          sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: c.dot }}
        />
      )}
      {name && (
        <Box component="span" sx={{ color: "text.primary", fontWeight: 500 }}>
          {name}
          <Box component="span" sx={{ color: "text.disabled", mx: 0.5 }}>
            ·
          </Box>
        </Box>
      )}
      <Box component="span" sx={{ color: c.fg, fontWeight: 600 }}>
        {verdict.display}
      </Box>
      {onClick && (
        <Iconify
          icon={selected ? "eva:chevron-up-fill" : "eva:chevron-down-fill"}
          width={14}
          sx={{ color: "text.disabled", ml: -0.25 }}
        />
      )}
    </ButtonBase>
  );
  if (!tooltip) return chip;
  return (
    <CustomTooltip show size="small" placement="top" arrow title={tooltip}>
      {chip}
    </CustomTooltip>
  );
};

EvalChip.propTypes = {
  name: PropTypes.string,
  verdict: PropTypes.shape({
    status: PropTypes.string.isRequired,
    display: PropTypes.string.isRequired,
  }).isRequired,
  selected: PropTypes.bool,
  onClick: PropTypes.func,
  tooltip: PropTypes.node,
};

export default EvalChip;
