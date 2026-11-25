import { useId } from "react";
import PropTypes from "prop-types";
import { Box, Typography, TextField } from "@mui/material";

export default function Field({ id, label, required, value, onChange, placeholder, helper, error, mono, type, multiline, fullWidth, autoComplete, inputProps }) {
  const generatedId = useId();
  const inputId = id || generatedId;
  const captionId = `${inputId}-caption`;
  // An error message takes over the helper line and turns the input red.
  const caption = error || helper;
  return (
    <Box sx={{ flex: fullWidth ? 1 : undefined }}>
      {label && (
        <Typography component="label" htmlFor={inputId} sx={{ display: "block", typography: "s1", fontWeight: 500, mb: 0.5 }}>
          {label}
          {required && <Box component="span" aria-hidden sx={{ color: "status.danger.fg", ml: 0.5 }}>*</Box>}
        </Typography>
      )}
      <TextField
        id={inputId}
        fullWidth size="small"
        error={!!error}
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        placeholder={placeholder}
        type={type}
        autoComplete={autoComplete}
        inputProps={{
          ...(required && { "aria-required": true }),
          ...(caption && { "aria-describedby": captionId }),
          ...inputProps,
        }}
        multiline={multiline}
        minRows={multiline ? 2 : undefined}
        sx={{
          ...(mono && {
            "& .MuiInputBase-input": { fontFamily: "ui-monospace, Menlo, monospace" },
          }),
        }}
      />
      {caption && (
        <Typography id={captionId} sx={{ typography: "s3", color: error ? "status.danger.fg" : "text.subtitle", mt: 0.5 }}>
          {caption}
        </Typography>
      )}
    </Box>
  );
}
Field.propTypes = {
  id: PropTypes.string,
  label: PropTypes.node, required: PropTypes.bool,
  value: PropTypes.string, onChange: PropTypes.func,
  placeholder: PropTypes.string, helper: PropTypes.node,
  error: PropTypes.node,
  mono: PropTypes.bool, type: PropTypes.string,
  multiline: PropTypes.bool, fullWidth: PropTypes.bool,
  autoComplete: PropTypes.string,
  inputProps: PropTypes.object,
};
