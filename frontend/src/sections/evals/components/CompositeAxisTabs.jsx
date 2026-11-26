import { Box, Tab, Typography } from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
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

/**
 * Maps a composite_child_axis value to the filters the eval picker
 * should apply so that only children of the matching shape are shown.
 * Every axis also locks template_type to "single" — a composite cannot
 * contain another composite.
 */
export function axisToLockedFilters(axis) {
  switch (axis) {
    case "pass_fail":
      return { output_type: ["pass_fail"], template_type: ["single"] };
    case "percentage":
      return { output_type: ["percentage"], template_type: ["single"] };
    case "choices":
      return { output_type: ["deterministic"], template_type: ["single"] };
    case "code":
      return { eval_type: ["code"], template_type: ["single"] };
    default:
      return null;
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
      <SegmentedTabs
        value={value || "pass_fail"}
        onChange={(_, val) => {
          if (disabled) return;
          onChange?.(val);
        }}
        sx={{ width: "fit-content" }}
      >
        {COMPOSITE_AXIS_OPTIONS.map((t) => (
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
          />
        ))}
      </SegmentedTabs>
    </Box>
  );
};

CompositeAxisTabs.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
  disabled: PropTypes.bool,
};

export default CompositeAxisTabs;
