import PropTypes from "prop-types";
import { useState } from "react";
import { Stack, Typography, Button, TextField } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { storeOfRef, validateSecretRef } from "./secretRefs";
import { MONO } from "./settings.constants";

// A secret's reference, or what stands in for one. Sample mode only: the API
// returns secret names, never where their values live.
export function SecretRefText({ value, empty = "not set" }) {
  const text = (
    <Typography
      noWrap
      sx={{
        typography: "s2",
        fontFamily: MONO,
        color: value ? "text.secondary" : "text.subtitle",
        minWidth: 0,
      }}
    >
      {value || empty}
    </Typography>
  );
  if (!value) return text;
  return (
    <CustomTooltip
      show
      arrow
      size="small"
      title={`${storeOfRef(value)?.label || "Secret manager"} · ${value}`}
    >
      {text}
    </CustomTooltip>
  );
}
SecretRefText.propTypes = { value: PropTypes.string, empty: PropTypes.string };

// "Set reference": a quiet text action, like the panel's other row actions.
export function SetRefAction({ onClick, disabled = false }) {
  return (
    <Typography
      role="button"
      aria-disabled={disabled || undefined}
      tabIndex={disabled ? -1 : 0}
      onClick={disabled ? undefined : onClick}
      onKeyDown={(e) => {
        if (!disabled && e.key === "Enter") onClick();
      }}
      sx={{
        typography: "s3",
        fontWeight: 600,
        flexShrink: 0,
        whiteSpace: "nowrap",
        color: disabled ? "text.disabled" : "primary.main",
        cursor: disabled ? "default" : "pointer",
      }}
    >
      Set reference
    </Typography>
  );
}
SetRefAction.propTypes = { onClick: PropTypes.func, disabled: PropTypes.bool };

// The inline editor. Accepts only a reference; a pasted key is refused with
// the reason.
export function SecretRefEditor({ placeholder, onSave, onCancel }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState(null);
  const save = () => {
    const res = validateSecretRef(value);
    if (!res.ok) {
      setError(res.error);
      return;
    }
    setError(null);
    onSave(res.ref);
  };
  return (
    <Stack spacing={0.75}>
      <Stack direction="row" alignItems="flex-start" spacing={1}>
        <TextField
          size="small"
          fullWidth
          autoFocus
          value={value}
          placeholder={placeholder || "vault://acme/sim/api_key"}
          onChange={(e) => {
            setValue(e.target.value);
            if (error) setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              save();
            }
            if (e.key === "Escape") onCancel?.();
          }}
          error={!!error}
          inputProps={{
            spellCheck: false,
            autoComplete: "off",
            "aria-label": "Secret reference",
          }}
          sx={{
            flex: 1,
            minWidth: 200,
            "& .MuiInputBase-root": { typography: "s2", fontFamily: MONO },
          }}
        />
        <Button
          size="small"
          variant="outlined"
          onClick={save}
          disabled={!value.trim()}
          sx={{ flexShrink: 0, color: "text.primary", borderColor: "divider" }}
        >
          Save reference
        </Button>
        {onCancel && (
          <Button
            size="small"
            onClick={onCancel}
            sx={{ flexShrink: 0, color: "text.secondary" }}
          >
            Cancel
          </Button>
        )}
      </Stack>
      <Typography
        role={error ? "alert" : undefined}
        sx={{ typography: "s3", color: error ? "error.main" : "text.subtitle" }}
      >
        {error ||
          "A secret-manager path (vault://, aws-sm://…). The value is never shown."}
      </Typography>
    </Stack>
  );
}
SecretRefEditor.propTypes = {
  placeholder: PropTypes.string,
  onSave: PropTypes.func.isRequired,
  onCancel: PropTypes.func,
};
