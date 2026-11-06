import {
  Box,
  FormControl,
  FormControlLabel,
  Radio,
  RadioGroup,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { Controller } from "react-hook-form";
import logger from "src/utils/logger";

const RadioField = ({
  custom = false,
  control,
  fieldName,
  label,
  options,
  optionDirection = "column",
  required,
  customLabel,
  hideRadioField = false,
  renderCustomOption, // New prop for rendering custom radio buttons
  helperText, // New prop for error handling
  ...other
}) => {
  return (
    <Controller
      render={({ field, fieldState: { error } }) => (
        <FormControl
          sx={{ ...other?.parentSx }}
          component="fieldset"
          error={!!error} // Highlight the field if there is an error
        >
          {customLabel}
          {label && (
            <Typography
              sx={{
                fontSize: "13px",
                fontWeight: "600",
                lineHeight: "18.2px",
                letterSpacing: "0.02em",
                color: other.labelColor || "text.secondary",
                marginBottom: "10px",
              }}
            >
              {label}
              {required && <span style={{ color: "red" }}>*</span>}
            </Typography>
          )}
          <RadioGroup
            {...field}
            value={field.value || ""} // Ensure the value is passed correctly
            onChange={(event) => {
              logger.debug("event", typeof event.target.value);

              // Find the original option to get the correct data type
              const selectedOption = options.find(
                (option) => String(option.value) === event.target.value,
              );
              const actualValue = selectedOption
                ? selectedOption.value
                : event.target.value;

              // Create a new event with the correct value
              const syntheticEvent = {
                ...event,
                target: {
                  ...event.target,
                  value: actualValue,
                },
              };

              field.onChange(actualValue); // Update form state with correct data type
              if (other.onChange) {
                other.onChange(syntheticEvent); // Call custom onChange if passed
              }
            }}
            aria-labelledby={label || "label"}
            sx={{
              display: "flex",
              ...(optionDirection === "column"
                ? {
                    flexDirection: "column",
                    marginBottom: "-18px",
                  }
                : {
                    flexDirection: "row",
                    marginBottom: "-18px",
                  }),
              padding: "10px",
              ...other.groupSx,
            }}
          >
            {options.map((option) => {
              const isSelected = field.value === option.value;

              if (renderCustomOption) {
                return renderCustomOption({
                  option,
                  isSelected,
                  onChange: (value) => {
                    const selectedOption = options.find(
                      (opt) => String(opt.value) === String(value),
                    );
                    const actualValue = selectedOption
                      ? selectedOption.value
                      : value;

                    const syntheticEvent = {
                      target: {
                        value: actualValue,
                      },
                    };

                    field.onChange(actualValue);
                    if (other.onChange) {
                      other.onChange(syntheticEvent);
                    }
                  },
                  error,
                });
              }

              return custom ? (
                <Box
                  key={option.value}
                  variant="outlined"
                  onClick={() =>
                    field.onChange({ target: { value: option.value } })
                  }
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    mb: 1,
                    py: 1.2,
                    marginLeft: 1,
                    height: "48px",
                    cursor: "pointer",
                    borderRadius: "4px",
                    backgroundColor: "background.paper",
                    border: "1px solid",
                    borderColor: "divider",

                    transition: "all 0.2s ease",
                    ...other?.sx,
                    ...(other?.getOptionSx?.(option, isSelected) || {}),
                  }}
                >
                  {!hideRadioField && (
                    <Radio
                      checked={isSelected}
                      value={option.value}
                      onChange={field.onChange}
                      sx={{
                        "& .MuiSvgIcon-root": { fontSize: "1.2rem" },
                        "&.Mui-checked": { color: "text.primary" },
                        mr: 1,
                      }}
                      disabled={option.disabled ?? false}
                    />
                  )}
                  <Typography
                    sx={{
                      fontSize: "13px",
                      fontWeight: 500,
                      color: "text.primary",
                      ...other?.labelTxtsx,
                    }}
                  >
                    {option.label}
                  </Typography>
                </Box>
              ) : (
                <FormControlLabel
                  key={option.value}
                  value={option.value}
                  control={
                    <Radio
                      sx={{
                        "& .MuiSvgIcon-root": {
                          fontSize: "1.2rem", // Adjust radio circle size
                        },
                        ...other.radioSx,
                      }}
                      disabled={option.disabled ?? false}
                    />
                  }
                  componentsProps={{
                    typography: {
                      typography: other.optionVariant ?? "body2",
                      fontWeight: other.optionFontWeight ?? "fontWeightRegular",
                      color: other.optionColor ?? "text.disabled",
                    },
                  }}
                  label={option.label}
                  sx={{
                    my: "-3px",
                    "&.MuiFormControlLabel-root": {
                      ml: 0, // Align label flush left
                    },
                    "& .MuiTypography-root": {
                      m: 0,
                      fontSize: "13px",
                    },
                    ...other.optionSx,
                  }}
                  {...other}
                />
              );
            })}
          </RadioGroup>
          {error && (
            <Typography
              sx={{
                color: "status.danger.fg",
                fontSize: "12px",
                marginTop: "5px",
                ...other.helperTextSx,
              }}
            >
              {error.message || helperText}
            </Typography>
          )}
        </FormControl>
      )}
      control={control}
      name={fieldName}
      rules={{ required: required && "This field is required" }} // Add validation rules
    />
  );
};

RadioField.propTypes = {
  control: PropTypes.any,
  fieldName: PropTypes.string.isRequired,
  required: PropTypes.bool,
  helperText: PropTypes.any, // Add prop type for helperText
  label: PropTypes.string || undefined,
  custom: PropTypes.bool,
  hideRadioField: PropTypes.bool,
  options: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string,
      value: PropTypes.oneOfType([
        PropTypes.string,
        PropTypes.number,
        PropTypes.bool,
      ]),
    }),
  ),
  optionDirection: PropTypes.string,
  customLabel: PropTypes.any,
  renderCustomOption: PropTypes.func, // Add prop type for renderCustomOption
};

export default RadioField;
