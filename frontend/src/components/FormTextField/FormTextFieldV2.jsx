import React from "react";
import PropTypes from "prop-types";
import { Controller } from "react-hook-form";
import { TextField } from "@mui/material";
import { SpinnerControls } from "src/components/SpinnerControls/SpinnerControls";
import { handleNumericInput } from "../ComplexFilter/common";

/**
 * A wrapper component for displaying MUI TextField with react-hook-form
 */
const FormTextFieldV2 = React.forwardRef(
  (
    {
      control,
      fieldName,
      helperText,
      sx = {},
      label,
      defaultValue,
      onBlur,
      onChange: propOnChange,
      fieldType = "text",
      // Filter ``shrink`` out of ``rest`` — it belongs only on InputLabel.
      // See note in rhf-text-field.jsx for why.
      // eslint-disable-next-line no-unused-vars
      shrink: _shrink,
      ...rest
    },
    ref,
  ) => {
    const isNumber = fieldType === "number";
    return (
      <Controller
        rules={{ required: rest.required }}
        render={({
          field: { onChange, value, onBlur: defaultBlur, ref: rhfRef },
          formState: { errors },
        }) => (
          <TextField
            type={fieldType}
            label={!rest.hiddenLabel ? label : null}
            onChange={(e) => {
              const newValue =
                fieldType === "number"
                  ? handleNumericInput(e.target.value)
                  : e.target.value;
              const parsedValue = isNumber ? parseFloat(newValue) : newValue;
              onChange(parsedValue);
              propOnChange?.(e);
            }}
            onBlur={() => {
              defaultBlur();
              onBlur?.();
            }}
            inputRef={(el) => {
              rhfRef(el);
              if (ref) {
                ref.current = el;
              }
            }}
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
              "& .MuiFormHelperText-root": {
                marginLeft: 0,
              },
              ...sx,
              ...(fieldType === "date" && {
                "& input::-webkit-calendar-picker-indicator": {
                  display: "none",
                },
                "& input::-webkit-clear-button": {
                  display: "none",
                },
              }),
            }}
            inputProps={{
              ...(isNumber
                ? {
                    style: {
                      textAlign: "left",
                      paddingRight: "40px",
                    },
                  }
                : {}),
              ...rest.inputProps,
            }}
            InputProps={{
              endAdornment: isNumber ? (
                <SpinnerControls
                  value={value}
                  onChange={(newValue) => {
                    onChange(newValue);
                  }}
                />
              ) : (
                rest.InputProps?.endAdornment
              ),
              ...rest.InputProps,
              sx: {
                ...(rest.InputProps?.sx || {}),
                "& input:-webkit-autofill, & input:-webkit-autofill:hover, & input:-webkit-autofill:focus":
                  {
                    WebkitBoxShadow:
                      "0 0 0 1000px var(--bg-paper) inset !important",
                    WebkitTextFillColor: "var(--text-primary) !important",
                    backgroundColor: "transparent !important",
                  },
              },
            }}
            InputLabelProps={{
              shrink: true,
              style: {
                display: "flex",
                paddingLeft: 2,
                paddingRight: 2,
                flexDirection: "row",
                alignItems: "center",
                background: "var(--bg-paper)",
              },
            }}
            {...rest}
          />
        )}
        control={control}
        name={fieldName}
        defaultValue={defaultValue}
      />
    );
  },
);

FormTextFieldV2.displayName = "FormTextFieldV2";

export default FormTextFieldV2;

FormTextFieldV2.propTypes = {
  control: PropTypes.any,
  fieldName: PropTypes.string.isRequired,
  helperText: PropTypes.any,
  label: PropTypes.string || PropTypes.element,
  sx: PropTypes.object,
  defaultValue: PropTypes.any,
  onBlur: PropTypes.func,
  fieldType: PropTypes.string,
  onChange: PropTypes.func,
  ref: PropTypes.object,
};
