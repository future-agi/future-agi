import { Box, Tab, Tabs, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

export const COMPOSITE_AXIS_OPTIONS = [
  {
    value: "pass_fail",
    label: "Pass / Fail",
    icon: "solar:check-circle-bold-duotone",
  },
  {
    value: "percentage",
    label: "Score",
    icon: "solar:graph-bold-duotone",
  },
  {
    value: "choices",
    label: "Choices",
    icon: "solar:list-check-bold-duotone",
  },
  {
    value: "code",
    label: "Code",
    icon: "solar:code-square-bold-duotone",
  },
];

// L7c g7 (evalcreate:composite): Create offers only judge evals that score
// Pass/Fail or 0-1 (no Choices, no Code type), so the child axis offers
// those two. A composite saved on another axis still shows its own.
const OFFERED_AXES = new Set(["pass_fail", "percentage"]);
export const axisOptionsFor = (value) =>
  COMPOSITE_AXIS_OPTIONS.filter(
    (t) => OFFERED_AXES.has(t.value) || t.value === value,
  );

/**
 * Maps a composite_child_axis value to the filters the eval picker
 * should apply so that only children of the matching shape are shown.
 * Every axis, including legacy unset axes, excludes composites and DAGs:
 * ordinary composite execution does not dispatch decision trees.
 */
export function axisToLockedFilters(axis) {
  const supportedChildren = {
    template_type: ["single"],
    eval_type_not: ["dag"],
  };
  switch (axis) {
    case "pass_fail":
      return { ...supportedChildren, output_type: ["pass_fail"] };
    case "percentage":
      return { ...supportedChildren, output_type: ["percentage"] };
    case "choices":
      return { ...supportedChildren, output_type: ["deterministic"] };
    case "code":
      return { ...supportedChildren, eval_type: ["code"] };
    default:
      return supportedChildren;
  }
}

/**
 * Pill-style tab selector for the composite child axis.
 * Shape mirrors `TaskDetailPage` tabs.
 */
const CompositeAxisTabs = ({ value, onChange, disabled = false }) => {
  return (
    <Box>
      <Typography variant="body2" fontWeight={600} sx={{ mb: 0.75 }}>
        Child evaluation type
      </Typography>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 1 }}
      >
        All children in a composite must produce the same kind of score so the
        aggregate number is comparable.
      </Typography>
      <Tabs
        value={value || "pass_fail"}
        onChange={(_, val) => {
          if (disabled) return;
          onChange?.(val);
        }}
        TabIndicatorProps={{ style: { display: "none" } }}
        sx={{
          minHeight: 32,
          "& .MuiTab-root": {
            minHeight: 32,
            px: 1.5,
            py: 0,
            mr: "0px !important",
            textTransform: "none",
            fontSize: "13px",
            borderRadius: "6px",
          },
          border: "1px solid",
          borderColor: "divider",
          p: "2px",
          borderRadius: "8px",
          width: "fit-content",
          bgcolor: (theme) =>
            theme.palette.mode === "dark"
              ? "rgba(255,255,255,0.04)"
              : "background.neutral",
        }}
      >
        {axisOptionsFor(value).map((t) => (
          <Tab
            key={t.value}
            value={t.value}
            disabled={disabled}
            label={
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
                <Iconify icon={t.icon} width={14} />
                {t.label}
              </Box>
            }
            sx={{
              bgcolor:
                value === t.value
                  ? (theme) =>
                      theme.palette.mode === "dark"
                        ? "rgba(255,255,255,0.12)"
                        : "background.paper"
                  : "transparent",
              boxShadow:
                value === t.value
                  ? (theme) =>
                      theme.palette.mode === "dark"
                        ? "none"
                        : "0 1px 3px rgba(0,0,0,0.08)"
                  : "none",
              borderRadius: "6px",
              fontWeight: value === t.value ? 600 : 400,
              color: value === t.value ? "text.primary" : "text.disabled",
            }}
          />
        ))}
      </Tabs>
    </Box>
  );
};

CompositeAxisTabs.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
  disabled: PropTypes.bool,
};

export default CompositeAxisTabs;
