import React, { useState } from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import InputAdornment from "@mui/material/InputAdornment";
import TextField from "@mui/material/TextField";

/**
 * Where a secret's value is typed or pasted. Masked by default, with Show for
 * checking a paste before it is saved. The value is taken exactly as entered (never trimmed) and
 * lives only in the dialog's state until it is sent; no reply ever returns it.
 * Browsers are asked not to store or suggest it.
 */
export default function SecretValueField({
  value,
  onChange,
  error,
  disabled,
  label = "Value",
  testId = "secret-value",
}) {
  const [shown, setShown] = useState(false);
  return (
    <TextField
      size="small"
      fullWidth
      label={label}
      type={shown ? "text" : "password"}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      error={Boolean(error)}
      helperText={
        error || "After saving, nobody can read it back, you included."
      }
      autoComplete="off"
      inputProps={{
        "aria-label": label,
        "data-testid": testId,
        autoComplete: "new-password",
        spellCheck: false,
        autoCapitalize: "off",
        autoCorrect: "off",
        "data-1p-ignore": "true",
        "data-lpignore": "true",
      }}
      InputProps={{
        sx: { fontFamily: "monospace" },
        endAdornment: (
          <InputAdornment position="end">
            <Button
              size="small"
              color="inherit"
              onClick={() => setShown((current) => !current)}
              aria-label={shown ? "Hide value" : "Show value"}
              aria-pressed={shown}
              sx={{ minWidth: 0, px: 1 }}
            >
              {shown ? "Hide" : "Show"}
            </Button>
          </InputAdornment>
        ),
      }}
    />
  );
}

SecretValueField.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  error: PropTypes.string,
  disabled: PropTypes.bool,
  label: PropTypes.string,
  testId: PropTypes.string,
};
