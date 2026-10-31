import PropTypes from "prop-types";
import { useState } from "react";
import { Box, InputAdornment, TextField, Typography } from "@mui/material";
import { isSecretRef, storeOfRef, validateSecretRef } from "./secretRefs";

const MONO = "ui-monospace, Menlo, monospace";
/* What happens to a credential, truthfully (RSI PRD, "Credentials"). A
   platform key's own use is said by the field that asks for it. */
const HELPER =
  "Stored encrypted and used only by Future AGI. Never shown again, never sent to the improver.";

/**
 * A credential field in a connect form: it takes the key itself, or a
 * reference into the team's secret manager (vault://…, aws-sm://…). A typed
 * key is stored encrypted and read back only where a test needs it; a
 * reference is resolved the same way. Either way the record keeps where it's
 * held, never the value, and it never reaches a PR, brief, scenario or log.
 */
export default function SecretRefField({
  label,
  required,
  value,
  onChange,
  placeholder,
  helper,
  fullWidth,
  dense,
}) {
  const [touched, setTouched] = useState(false);
  const text = value || "";
  const store = isSecretRef(text) ? storeOfRef(text.trim()) : null;
  /* Only something written like a reference (a scheme and ://) can be a
     malformed one — anything else is the key itself. A half-typed path
     isn't wrong yet, so it's said once the field is left. */
  const formatError =
    touched && !store && /:\/\//.test(text)
      ? validateSecretRef(text).error
      : null;

  return (
    <Box sx={{ flex: fullWidth ? 1 : undefined, minWidth: 0 }}>
      {label && (
        <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>
          {label}
          {required && (
            <Box component="span" sx={{ color: "error.main", ml: 0.5 }}>
              *
            </Box>
          )}
        </Typography>
      )}
      <TextField
        fullWidth
        size="small"
        value={text}
        placeholder={`Paste the key, or ${placeholder || "vault://acme/sim/api_key"}`}
        onChange={(e) => onChange(e.target.value)}
        onBlur={() => setTouched(true)}
        error={!!formatError}
        inputProps={{
          spellCheck: false,
          autoComplete: "off",
          "aria-label":
            typeof label === "string" ? label : "Key or secret reference",
        }}
        InputProps={
          store
            ? {
                endAdornment: (
                  <InputAdornment position="end">
                    <Typography
                      noWrap
                      sx={{ typography: "s3", color: "text.subtitle" }}
                    >
                      {store.label}
                    </Typography>
                  </InputAdornment>
                ),
              }
            : undefined
        }
        sx={{ "& .MuiInputBase-input": { typography: "s2", fontFamily: MONO } }}
      />
      {dense && !formatError ? null : (
        <Typography
          role={formatError ? "alert" : undefined}
          sx={{
            typography: "s3",
            color: formatError ? "error.main" : "text.subtitle",
            mt: 0.5,
          }}
        >
          {formatError || helper || HELPER}
        </Typography>
      )}
    </Box>
  );
}

SecretRefField.propTypes = {
  label: PropTypes.node,
  required: PropTypes.bool,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  placeholder: PropTypes.string,
  helper: PropTypes.node,
  fullWidth: PropTypes.bool,
  /* In a list of several: no helper line unless something's wrong. */
  dense: PropTypes.bool,
};
