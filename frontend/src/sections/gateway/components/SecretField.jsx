/* eslint-disable react/prop-types */
import React, { useRef } from "react";
import { TextField, InputAdornment, Button } from "@mui/material";

// How every read path returns a stored org-config secret: the mask
// `__encrypted__` plus the 16-hex fingerprint of the stored ciphertext
// (agentcc/services/org_config_secrets.py `_mask`). It is not the secret and
// is never shown as an editable value. Only that exact shape
// counts: no read path returns ciphertext, and a secret may be any string, so
// a typed value that merely starts with `enc::` or `__encrypted__` is a value.
const MASK_PATTERN = /^__encrypted__[0-9a-f]{16}$/;
const PLACEHOLDER_DOTS = "••••••••••••";

export function isMaskedSecret(value) {
  return typeof value === "string" && MASK_PATTERN.test(value);
}

// A write-only secret input. When a secret is stored (the value is a mask) it
// shows placeholder dots with Replace and Remove actions instead of the value;
// the value a reader was shown is never rendered or round-tripped as the
// field's value. Replace clears the draft to "", which the backend reads as
// keep-on-blank, so leaving it blank keeps the stored secret and typing a
// value replaces it. Remove sends null, which the backend deletes (a merge
// patch turns a present-then-null leaf into a removal), so a stored secret can
// still be taken away. A value this field produced from typing is never taken
// for a stored secret, whatever it looks like, so the input never turns into
// dots mid-entry; a mask that arrives from outside (a load, a reset) does.
const SecretField = ({
  value,
  onChange,
  label,
  placeholder = "Enter a value",
  helperText,
  size = "small",
  sx,
  fullWidth = false,
  disabled = false,
  removable = true,
}) => {
  const typed = useRef(undefined);
  const stored = isMaskedSecret(value) && value !== typed.current;

  if (stored) {
    return (
      <TextField
        label={label}
        size={size}
        sx={sx}
        fullWidth={fullWidth}
        value={PLACEHOLDER_DOTS}
        inputProps={{ "aria-label": label, readOnly: true }}
        InputProps={{
          readOnly: true,
          endAdornment: (
            <InputAdornment position="end">
              <Button
                size="small"
                onClick={() => onChange("")}
                disabled={disabled}
              >
                Replace
              </Button>
              {removable && (
                <Button
                  size="small"
                  color="error"
                  onClick={() => onChange(null)}
                  disabled={disabled}
                >
                  Remove
                </Button>
              )}
            </InputAdornment>
          ),
        }}
        helperText={
          helperText ||
          "Stored and never shown. Replace to change, Remove to delete, or leave blank to keep."
        }
      />
    );
  }

  return (
    <TextField
      label={label}
      size={size}
      sx={sx}
      fullWidth={fullWidth}
      type="password"
      autoComplete="new-password"
      value={value || ""}
      placeholder={placeholder}
      onChange={(e) => {
        typed.current = e.target.value;
        onChange(e.target.value);
      }}
      disabled={disabled}
      helperText={helperText}
    />
  );
};

export default SecretField;
