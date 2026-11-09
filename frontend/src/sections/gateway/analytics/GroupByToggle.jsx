import React from "react";
import PropTypes from "prop-types";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";

// "Group by" for an analytics tab: one compact segmented control on the
// theme's type scale.
export default function GroupByToggle({
  value,
  options,
  onChange,
  label = "Group by",
}) {
  return (
    <ToggleButtonGroup
      value={value}
      exclusive
      size="small"
      aria-label={label}
      onChange={(_event, next) => next !== null && onChange(next)}
      sx={{
        flexWrap: "wrap",
        "& .MuiToggleButton-root": { px: 1.25, py: 0.25, typography: "s2" },
      }}
    >
      {options.map((opt) => (
        <ToggleButton key={opt.value} value={opt.value}>
          {opt.label}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
}

GroupByToggle.propTypes = {
  value: PropTypes.string.isRequired,
  options: PropTypes.arrayOf(
    PropTypes.shape({ value: PropTypes.string, label: PropTypes.string }),
  ).isRequired,
  onChange: PropTypes.func.isRequired,
  label: PropTypes.string,
};
