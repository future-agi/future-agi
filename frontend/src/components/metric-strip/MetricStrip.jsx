import React from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Tooltip, Typography } from "@mui/material";

import { STATUS_ROLES } from "src/utils/runStatus";

// Future AGI Design System · Metric strip. A page's few headline numbers in
// one bordered strip divided by hairlines — not a row of separate cards.
// Label in caption/fg.tertiary, value 20px/600 in fg.default with tabular
// figures, optional change. Colour goes only on the change or a verdict hint
// (role), never on the number itself.

const DELTA = { better: "status.success.fg", worse: "status.danger.fg", flat: "text.tertiary" };

// With `wrap` the strip goes two to a row on small screens instead of squeezing.
export default function MetricStrip({ items, wrap = false, sx }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))`,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        // The 1px gaps show the divider colour, so the lines hold in both layouts.
        ...(wrap && {
          gridTemplateColumns: {
            xs: "repeat(2, minmax(0, 1fr))",
            md: `repeat(${items.length}, minmax(0, 1fr))`,
          },
          gap: "1px",
          bgcolor: "divider",
          overflow: "hidden",
        }),
        ...sx,
      }}
    >
      {items.map((m, i) => (
        <Box
          key={m.key || (typeof m.label === "string" ? m.label : i)}
          {...(m.onClick && { component: ButtonBase, onClick: m.onClick })}
          sx={{
            px: 2,
            py: 1.5,
            borderLeft: i && !wrap ? "1px solid" : 0,
            borderColor: "divider",
            ...(wrap && { bgcolor: "background.default" }),
            minWidth: 0,
            display: "block",
            textAlign: "left",
            ...(m.onClick && { "&:hover": { bgcolor: "action.hover" } }),
          }}
        >
          <Tooltip title={m.tooltip || ""} disableHoverListener={!m.tooltip} placement="top-start" arrow>
            <Typography noWrap sx={{ fontSize: 12, lineHeight: "16px", color: "text.tertiary" }}>
              {m.label}
            </Typography>
          </Tooltip>
          <Box sx={{ display: "flex", alignItems: "baseline", gap: 0.75, mt: 0.5 }}>
            <Typography
              noWrap={wrap}
              sx={{
                fontSize: 20,
                lineHeight: "28px",
                fontWeight: 600,
                color: "text.primary",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {m.value}
            </Typography>
            {m.change && (
              <Typography
                sx={{ fontSize: 12, fontWeight: 500, color: DELTA[m.trend] || "text.tertiary" }}
              >
                {m.change}
              </Typography>
            )}
          </Box>
          {m.hint && (
            <Typography
              noWrap
              sx={{
                fontSize: 12,
                color: m.hintRole ? `status.${m.hintRole}.fg` : "text.tertiary",
              }}
            >
              {m.hint}
            </Typography>
          )}
        </Box>
      ))}
    </Box>
  );
}

MetricStrip.propTypes = {
  items: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.node.isRequired,
      value: PropTypes.node,
      change: PropTypes.node,
      trend: PropTypes.oneOf(["better", "worse", "flat"]),
      hint: PropTypes.node,
      // Colours a verdict hint (e.g. "Slow") by status role.
      hintRole: PropTypes.oneOf(STATUS_ROLES),
      tooltip: PropTypes.node,
      onClick: PropTypes.func,
      key: PropTypes.string,
    }),
  ).isRequired,
  wrap: PropTypes.bool,
  sx: PropTypes.object,
};
