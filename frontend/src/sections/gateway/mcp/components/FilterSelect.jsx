import React from "react";
import PropTypes from "prop-types";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";

// One filter in a gateway filter bar: an MUI (never native) select at the
// filter bar's 30 px height. `options` are { value, label }; the empty value
// is the "All" choice. `disabled` keeps the filter in the bar, unusable.
export default function FilterSelect({
  label,
  value,
  onChange,
  options,
  allLabel = "All",
  minWidth = 150,
  disabled = false,
}) {
  return (
    <TextField
      select
      size="small"
      label={label}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      // The "All" choice is the empty value: show it, with the label raised.
      SelectProps={{
        displayEmpty: allLabel !== null,
        MenuProps: { disableScrollLock: true },
      }}
      InputLabelProps={{ shrink: true }}
      sx={{
        minWidth,
        flex: { xs: "1 1 140px", sm: "0 0 auto" },
        "& .MuiOutlinedInput-root": { height: "30px", typography: "s2" },
      }}
    >
      {allLabel !== null && <MenuItem value="">{allLabel}</MenuItem>}
      {options.map((option) => (
        <MenuItem key={option.value} value={option.value}>
          {option.label}
        </MenuItem>
      ))}
    </TextField>
  );
}

FilterSelect.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  options: PropTypes.arrayOf(
    PropTypes.shape({ value: PropTypes.string, label: PropTypes.node }),
  ).isRequired,
  allLabel: PropTypes.string,
  minWidth: PropTypes.number,
  disabled: PropTypes.bool,
};
