// Shared chart pieces for the five Workbench charts (dataviz method): one
// palette (run colours from runColors.js, direct run labels as the second
// encoding because pink/blue fail CVD separation), recessive axes and
// grid, a hover tooltip on every mark, the empty state.
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";

export const CHART_H = 300;
export const EMPTY_CHART = "Nothing to chart for this grouping.";

/** Axis / grid colours for the current mode (muted grid in dark). */
export function useChartTheme() {
  const theme = useTheme();
  const dark = theme.palette.mode === "dark";
  return {
    dark,
    grid: dark
      ? alpha(theme.palette.common.white, 0.08)
      : theme.palette.divider,
    axis: theme.palette.text.secondary,
    ink: theme.palette.text.primary,
    muted: theme.palette.text.disabled,
    surface: theme.palette.background.paper,
    primary: theme.palette.primary.main,
    tick: { fontSize: 12, fill: theme.palette.text.secondary },
  };
}

/** The card a chart sits in: title line, legend line, plot, footer. */
export function ChartFrame({ title, legend, children, footer, empty }) {
  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        px: 1.5,
        pt: 1,
        pb: 1,
      }}
    >
      <Stack
        direction="row"
        alignItems="baseline"
        justifyContent="space-between"
        gap={1}
        flexWrap="wrap"
      >
        <Typography typography="s1" fontWeight="fontWeightSemiBold">
          {title}
        </Typography>
        {legend}
      </Stack>
      {empty ? (
        <Stack
          alignItems="center"
          justifyContent="center"
          sx={{ height: CHART_H }}
        >
          <Typography typography="s1" color="text.secondary">
            {EMPTY_CHART}
          </Typography>
        </Stack>
      ) : (
        <Box sx={{ height: CHART_H, mt: 0.5 }}>{children}</Box>
      )}
      {footer}
    </Box>
  );
}

ChartFrame.propTypes = {
  title: PropTypes.node,
  legend: PropTypes.node,
  children: PropTypes.node,
  footer: PropTypes.node,
  empty: PropTypes.bool,
};

/** Legend: colour square + run label (+ optional note), text in ink. */
export function RunLegend({ items }) {
  if (!items?.length || items.length < 2) return null;
  return (
    <Stack direction="row" gap={1.25} flexWrap="wrap" alignItems="center">
      {items.map((it) => (
        <Stack key={it.key} direction="row" alignItems="center" gap={0.5}>
          <Box
            sx={{
              width: 10,
              height: 10,
              borderRadius: "2px",
              bgcolor: it.color,
            }}
          />
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {it.label}
            {it.note ? ` ${it.note}` : ""}
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
}

RunLegend.propTypes = { items: PropTypes.array };

/** Tooltip body (recharts `content`): lines of text in ink. */
export function TipBox({ lines }) {
  if (!lines?.length) return null;
  return (
    <Box
      sx={{
        bgcolor: "background.paper",
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        boxShadow: (t) => t.customShadows?.z8 ?? 3,
        px: 1,
        py: 0.75,
        maxWidth: 320,
      }}
    >
      {lines.filter(Boolean).map((l, i) => (
        <Typography
          key={i}
          typography={i === 0 ? "s2" : "s3"}
          fontWeight={i === 0 ? 600 : 400}
          color={i === 0 ? "text.primary" : "text.secondary"}
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {l}
        </Typography>
      ))}
    </Box>
  );
}

TipBox.propTypes = { lines: PropTypes.array };

/** Colour of a column (run colour, else a neutral grey by index). */
export function colColor(colColors, col, theme) {
  return (
    colColors?.[col.key]?.solid ??
    colColors?.[col.runIds?.[0]]?.solid ??
    theme.muted
  );
}
