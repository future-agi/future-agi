import PropTypes from "prop-types";
import { Box, Typography, TextField } from "@mui/material";

export default function Field({ label, required, value, onChange, placeholder, helper, mono, type, multiline, fullWidth, autoComplete }) {
  return (
    <Box sx={{ flex: fullWidth ? 1 : undefined }}>
      {label && (
        <Typography sx={{ typography: "s3", fontWeight: "fontWeightSemiBold", mb: 0.5 }}>
          {label}
          {required && <Box component="span" sx={{ color: "error.main", ml: 0.5 }}>*</Box>}
        </Typography>
      )}
      <TextField
        fullWidth size="small"
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        placeholder={placeholder}
        type={type}
        autoComplete={autoComplete}
        multiline={multiline}
        minRows={multiline ? 2 : undefined}
        sx={{
          "& .MuiInputBase-input": {
            typography: "s2",
            ...(mono && { fontFamily: "ui-monospace, Menlo, monospace" }),
          },
        }}
      />
      {helper && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
          {helper}
        </Typography>
      )}
    </Box>
  );
}
Field.propTypes = {
  label: PropTypes.node, required: PropTypes.bool,
  value: PropTypes.string, onChange: PropTypes.func,
  placeholder: PropTypes.string, helper: PropTypes.node,
  mono: PropTypes.bool, type: PropTypes.string,
  multiline: PropTypes.bool, fullWidth: PropTypes.bool,
  autoComplete: PropTypes.string,
};
