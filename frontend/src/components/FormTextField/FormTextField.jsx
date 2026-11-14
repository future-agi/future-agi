import React from "react";
import PropTypes from "prop-types";
import { Controller } from "react-hook-form";
import { TextField, Typography } from "@mui/material";
import { SpinnerControls } from "src/components/SpinnerControls/SpinnerControls";
import { handleNumericInput } from "../ComplexFilter/common";
import { red } from "src/theme/palette";

/**
 * A wrapper component for displaying MUI TextField with react-hook-form
 */
export const FormTextField = ({
  control,
  fieldName,
  helperText,
  sx = {},
  label,
  defaultValue,
  isSpinnerField,
  onBlur,
  fieldType = "text",
  required,
  ...rest
}) => {
  // Add custom styles for required asterisk
  const requiredAsteriskStyles = required
    ? {
        "& .MuiFormLabel-asterisk": {
          color: red[500],
        },
      }
    : {};

  return (
    <Controller
      render={({
        field: { onChange, value, onBlur: defaultBlur, ref },
        formState: { errors },
      }) => (
        <TextField
          {...rest}
          required={required}
          type={isSpinnerField ? "number" : rest.type}
          label={
            !rest.hiddenLabel && label ? (
              <Typography
                typography="m3"
                fontWeight={"fontWeightMedium"}
                color={rest?.error ? "error.main" : "text.disabled"}
                sx={{
                  bgcolor: "background.paper",
                }}
              >
                {label?.endsWith("*") ? (
                  <>
                    {label.slice(0, -1)}
                    <Typography component="span" color="status.danger.fg">
                      *
                    </Typography>
                  </>
                ) : (
                  label
                )}
              </Typography>
            ) : null
          }
          onChange={(e) => {
            const newValue =
              fieldType === "number"
                ? handleNumericInput(e.target.value)
                : e.target.value;
            const parsedValue = isSpinnerField
              ? parseFloat(newValue)
              : newValue;
            rest?.onChange?.(e);
            onChange(parsedValue);
          }}
          onBlur={() => {
            defaultBlur();
            onBlur?.();
          }}
          inputRef={ref}
          value={value}
          error={
            !!fieldName.split(".").reduce((obj, key) => obj?.[key], errors)
              ?.message || rest?.error
          }
          helperText={
            fieldName.split(".").reduce((obj, key) => obj?.[key], errors)
              ?.message || helperText
          }
          sx={{
            "& .MuiOutlinedInput-notchedOutline legend": {
              width:
                (label?.length || 0) > 7
                  ? `${(label?.length || 0) - 1}ch`
                  : `${label?.length || 0}ch`,
            },
            input: { color: "text.secondary" },
            textarea: { color: "text.secondary" },
            "& .MuiFormLabel-root": {
              color: "green",
              paddingRight: 1,
            },
            // '& .MuiOutlinedInput-root.Mui-focused .MuiInputBase-input::placeholder': {
            //   color: 'blue',
            // },
            ...requiredAsteriskStyles,
            ...sx,
          }}
          InputProps={{
            ...rest.InputProps,
            inputProps: {
              ...rest.inputProps,
              ...(isSpinnerField
                ? {
                    min: 1,
                    style: {
                      textAlign: "left",
                      paddingRight: "40px",
                    },
                  }
                : {}),
            },
            endAdornment: isSpinnerField ? (
              <SpinnerControls
                value={value}
                onChange={(newValue) => {
                  onChange(newValue);
                }}
              />
            ) : (
              rest.InputProps?.endAdornment
            ),
          }}
        />
      )}
      control={control}
      name={fieldName}
      defaultValue={defaultValue}
    />
  );
};

FormTextField.propTypes = {
  control: PropTypes.any,
  fieldName: PropTypes.string.isRequired,
  helperText: PropTypes.any,
  label: PropTypes.string || PropTypes.element,
  sx: PropTypes.object,
  defaultValue: PropTypes.any,
  isSpinnerField: PropTypes.bool,
  onBlur: PropTypes.func,
  fieldType: PropTypes.oneOf(["text", "number"]),
  required: PropTypes.bool,
};
