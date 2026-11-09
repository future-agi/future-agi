import React from "react";
import PropTypes from "prop-types";
import { Box, Tooltip, Typography } from "@mui/material";
import { dsRadius, dsTypography } from "src/theme/ds-tokens";

// Adapted from Vel 05a5f9f77a. Actual result measures only, never navigation
// counts or object facts. Missing values are a dash, not a fabricated zero.
export default function MetricStrip({ items, sx }) {
  if (!items.length) return null;
  return (
    <Box
      component="dl"
      sx={{
        display: "grid",
        gridTemplateColumns: {
          xs: "1fr",
          sm: `repeat(${items.length}, minmax(0, 1fr))`,
        },
        m: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: `${dsRadius.lg}px`,
        ...sx,
      }}
    >
      {items.map((item, index) => (
        <Box
          key={
            item.key || (typeof item.label === "string" ? item.label : index)
          }
          sx={{
            px: 1.5,
            py: 1.5,
            minWidth: 0,
            borderColor: "divider",
            "&:not(:first-of-type)": {
              borderLeft: { xs: 0, sm: "1px solid" },
              borderTop: { xs: "1px solid", sm: 0 },
              borderColor: "divider",
            },
          }}
        >
          <Tooltip
            title={item.tooltip || ""}
            disableHoverListener={!item.tooltip}
            placement="top-start"
            arrow
          >
            <Typography
              component="dt"
              sx={{ ...dsTypography.caption, color: "text.tertiary" }}
            >
              {item.label}
            </Typography>
          </Tooltip>
          <Typography
            component="dd"
            sx={{
              ...dsTypography.metric_lg,
              m: 0,
              mt: 0.5,
              color: "text.primary",
              fontVariantNumeric: "tabular-nums",
              overflowWrap: "anywhere",
            }}
          >
            {item.value ?? "—"}
          </Typography>
          {item.change != null && (
            <Typography
              sx={{ ...dsTypography.caption, color: "text.secondary" }}
            >
              {item.change}
              {item.trend ? ` (${item.trend})` : ""}
            </Typography>
          )}
          {item.hint && (
            <Typography
              sx={{ ...dsTypography.caption, color: "text.tertiary" }}
            >
              {item.hint}
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
      key: PropTypes.string,
      label: PropTypes.node.isRequired,
      value: PropTypes.node,
      change: PropTypes.node,
      trend: PropTypes.oneOf(["better", "worse", "flat"]),
      hint: PropTypes.node,
      tooltip: PropTypes.node,
    }),
  ).isRequired,
  sx: PropTypes.object,
};
