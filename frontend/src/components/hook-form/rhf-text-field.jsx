import React, { useEffect, useRef, useCallback } from "react";
import PropTypes from "prop-types";
import { Controller, useFormContext } from "react-hook-form";

import TextField from "@mui/material/TextField";

// ----------------------------------------------------------------------

export default function RHFTextField({
  name,
  helperText,
  type,
  autoComplete,
  sx,
  InputProps: consumerInputProps,
  InputLabelProps: consumerInputLabelProps,
  // Filter out ``shrink`` so it can't leak via ``{...other}`` onto TextField
  // and then to the underlying DOM input. ``shrink`` belongs on InputLabel
  // only — passing it as a top-level prop triggers React's
  // "Received true for a non-boolean attribute" warning that floods the
  // console when many fields render at once (e.g. on drawer open).
  // eslint-disable-next-line no-unused-vars
  shrink: _shrink,
  ...other
}) {
  const { control, setValue, getValues } = useFormContext();
  const inputRef = useRef(null);

  // Optimized onChange handler
  const handleChange = useCallback(
    (field) => (event) => {
      if (type === "number") {
        field.onChange(Number(event.target.value));
      } else {
        field.onChange(event.target.value);
      }
    },
    [type],
  );

  // Autofill detection effect
  useEffect(() => {
    const syncInputValue = () => {
      if (inputRef.current) {
        const input = inputRef.current.querySelector("input");
        if (
          input &&
          input.value !== undefined &&
          input.value !== getValues(name)
        ) {
          setValue(name, input.value, { shouldValidate: true });
        }
      }
    };

    // Check for autofill after delays
    const timeouts = [
      setTimeout(syncInputValue, 100),
      setTimeout(syncInputValue, 500),
      setTimeout(syncInputValue, 1000),
    ];

    // Listen for autofill animation events
    const handleAnimationStart = (e) => {
      if (e.animationName === "onAutoFillStart" && e.target.name === name) {
        setTimeout(syncInputValue, 10);
      }
    };

    document.addEventListener("animationstart", handleAnimationStart);

    // Native change listener for more reliable autofill detection
    const input = inputRef.current?.querySelector("input");
    if (input) {
      input.addEventListener("change", syncInputValue);
    }

    return () => {
      timeouts.forEach(clearTimeout);
      document.removeEventListener("animationstart", handleAnimationStart);
      if (input) {
        input.removeEventListener("change", syncInputValue);
      }
    };
  }, [name, setValue, getValues]);

  const autofillStyles = {
    // Autofill styling — use CSS vars so colours follow the active theme
    "& input:-webkit-autofill": {
      WebkitBoxShadow: "0 0 0 1000px var(--bg-paper) inset !important",
      WebkitTextFillColor: "var(--text-primary) !important",
      backgroundColor: "transparent !important",
      animationName: "onAutoFillStart",
      animationDuration: "0.001s",
    },
    "& input:-webkit-autofill:hover": {
      WebkitBoxShadow: "0 0 0 1000px var(--bg-paper) inset !important",
      WebkitTextFillColor: "var(--text-primary) !important",
    },
    "& input:-webkit-autofill:focus": {
      WebkitBoxShadow: "0 0 0 1000px var(--bg-paper) inset !important",
      WebkitTextFillColor: "var(--text-primary) !important",
    },
    "& input:not(:-webkit-autofill)": {
      animationName: "onAutoFillCancel",
      animationDuration: "0.001s",
    },
  };

  return (
    <>
      {/* CSS animations for autofill detection */}
      <style>
        {`
          @keyframes onAutoFillStart {
            from { /*empty*/ }
            to { /*empty*/ }
          }
          @keyframes onAutoFillCancel {
            from { /*empty*/ }
            to { /*empty*/ }
          }
        `}
      </style>

      <Controller
        name={name}
        control={control}
        render={({ field, fieldState: { error } }) => (
          <TextField
            {...field}
            ref={inputRef}
            fullWidth
            type={type}
            autoComplete={autoComplete}
            value={type === "number" && field.value === 0 ? "" : field.value}
            onChange={handleChange(field)}
            error={!!error}
            helperText={error ? error?.message : helperText}
            sx={{
              "& .MuiInputBase-input": {
                caretColor: "currentColor",
              },
              ...sx,
            }}
            InputProps={{
              ...(consumerInputProps || {}),
              sx: {
                ...(consumerInputProps?.sx || {}),
                ...autofillStyles,
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
              ...(consumerInputLabelProps || {}),
            }}
            {...other}
          />
        )}
      />
    </>
  );
}

RHFTextField.propTypes = {
  autoComplete: PropTypes.string,
  helperText: PropTypes.object,
  InputLabelProps: PropTypes.object,
  InputProps: PropTypes.object,
  name: PropTypes.string,
  sx: PropTypes.object,
  type: PropTypes.string,
};
