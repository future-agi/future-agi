import React from "react";
import PropTypes from "prop-types";
import { Box, Tooltip, Typography } from "@mui/material";
import { STATUS_ROLES } from "src/utils/runStatus";
import { dsRadius, dsTypography } from "src/theme/ds-tokens";

// Adapted from Vel 05a5f9f77a. Actual result measures only, never navigation
// counts or object facts. Missing values are a dash, not a fabricated zero.
const DELTA = {
  better: "status.success.fg",
  worse: "status.danger.fg",
  flat: "text.tertiary",
};

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
          role={item.onClick ? "button" : undefined}
          tabIndex={item.onClick ? 0 : undefined}
          onClick={item.onClick}
          onKeyDown={
            item.onClick
              ? (event) => {
                  if (
                    event.target === event.currentTarget &&
                    ["Enter", " "].includes(event.key)
                  ) {
                    event.preventDefault();
                    item.onClick(event);
                  }
                }
              : undefined
          }
          sx={{
            ...(item.onClick && {
              cursor: "pointer",
              "&:hover": { bgcolor: "action.hover" },
              "&:focus-visible": {
                outline: "2px solid",
                outlineColor: "primary.main",
                outlineOffset: -2,
              },
            }),
            px: 2,
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
              sx={{
                ...dsTypography.caption,
                fontSize: 12,
                lineHeight: "16px",
                color: "text.tertiary",
              }}
            >
              {item.label}
            </Typography>
          </Tooltip>
          <Typography
            component="dd"
            sx={{
              ...dsTypography.metric_lg,
              fontSize: 20,
              lineHeight: "28px",
              fontWeight: 600,
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
              sx={{
                ...dsTypography.caption,
                fontSize: 12,
                fontWeight: 500,
                color: DELTA[item.trend] || "text.tertiary",
              }}
            >
              {item.change}
              {item.trend ? ` (${item.trend})` : ""}
            </Typography>
          )}
          {item.hint && (
            <Typography
              sx={{
                ...dsTypography.caption,
                fontSize: 12,
                lineHeight: "16px",
                color: item.hintRole
                  ? `status.${item.hintRole}.fg`
                  : "text.tertiary",
              }}
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
      hintRole: PropTypes.oneOf(STATUS_ROLES),
      onClick: PropTypes.func,
      tooltip: PropTypes.node,
    }),
  ).isRequired,
  sx: PropTypes.object,
};
