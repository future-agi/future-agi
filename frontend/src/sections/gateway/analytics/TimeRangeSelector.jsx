import React from "react";
import PropTypes from "prop-types";
import { ToggleButton, ToggleButtonGroup } from "@mui/material";

const TIME_RANGE_OPTIONS = [
  { value: "1h", label: "1H" },
  { value: "6h", label: "6H" },
  { value: "24h", label: "24H" },
  { value: "7d", label: "7D" },
  { value: "30d", label: "30D" },
  { value: "90d", label: "90D" },
];

const TimeRangeSelector = ({ value, onChange }) => {
  const handleChange = (_event, newValue) => {
    if (newValue !== null) {
      onChange(newValue);
    }
  };

  return (
    <ToggleButtonGroup
      value={value}
      exclusive
      onChange={handleChange}
      size="small"
      aria-label="Time range"
    >
      {TIME_RANGE_OPTIONS.map((option) => (
        <ToggleButton key={option.value} value={option.value}>
          {option.label}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
};

TimeRangeSelector.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};

export default TimeRangeSelector;
