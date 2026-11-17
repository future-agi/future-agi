import React from "react";
import PropTypes from "prop-types";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";

// The gateway's one time-range control (Analytics, Guardrails analytics): a
// value switch, so a toggle group, never a second strip of tabs.
export default function RangeToggle({ value, options, onChange }) {
  return (
    <ToggleButtonGroup
      value={value}
      exclusive
      size="small"
      aria-label="Time range"
      onChange={(_event, next) => next && onChange(next)}
      sx={{
        alignSelf: "flex-start",
        "& .MuiToggleButton-root": { px: 1.25, py: 0.25, typography: "s2" },
      }}
    >
      {options.map((option) => (
        <ToggleButton key={option.value} value={option.value}>
          {option.label}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
}

RangeToggle.propTypes = {
  value: PropTypes.string.isRequired,
  options: PropTypes.arrayOf(
    PropTypes.shape({
      value: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
    }),
  ).isRequired,
  onChange: PropTypes.func.isRequired,
};
