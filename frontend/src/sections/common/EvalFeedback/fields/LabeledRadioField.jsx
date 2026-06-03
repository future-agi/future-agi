import React from "react";
import PropTypes from "prop-types";
import {
  FormControl,
  FormControlLabel,
  FormHelperText,
  Radio,
  RadioGroup,
  Typography,
} from "@mui/material";
import { Controller } from "react-hook-form";
import { fieldLabelSx } from "./labelSx";

const radioGroupSx = {
  borderRadius: "8px",
  border: "1px solid var(--border-default)",
  padding: "10px",
  gap: "12px",
};

const radioOptionSx = {
  alignItems: "start",
  "& .MuiRadio-root	": {
    marginTop: "-6px",
  },
};

const LabeledRadioField = ({ control, fieldName, label, options, ...other }) => {
  return (
    <Controller
      render={({ field, fieldState: { error } }) => (
        <FormControl component="fieldset" error={!!error}>
          {label && <Typography sx={fieldLabelSx}>{label}</Typography>}
          <RadioGroup
            {...field}
            aria-labelledby={label || "label"}
            {...other}
            sx={radioGroupSx}
          >
            {options.map((option) => (
              <FormControlLabel
                key={option.value}
                value={option.value}
                control={<Radio />}
                label={option.label}
                disabled={Boolean(option.disabled)}
                sx={radioOptionSx}
              />
            ))}
          </RadioGroup>
          {error && <FormHelperText>{error.message}</FormHelperText>}
        </FormControl>
      )}
      control={control}
      name={fieldName}
    />
  );
};

LabeledRadioField.propTypes = {
  control: PropTypes.any,
  fieldName: PropTypes.string.isRequired,
  label: PropTypes.string,
  options: PropTypes.arrayOf(
    PropTypes.shape({
      value: PropTypes.oneOfType([PropTypes.string, PropTypes.number])
        .isRequired,
      label: PropTypes.node.isRequired,
      disabled: PropTypes.bool,
    })
  ).isRequired,
};

export default LabeledRadioField;
