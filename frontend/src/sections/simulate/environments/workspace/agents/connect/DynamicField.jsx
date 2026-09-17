import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box, Stack, Typography, TextField, InputAdornment, IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { FIELD } from "./connect.constants";

// Renders one field from a reach's connection schema.
//
// The schema lives with the reach (connect.constants REACH_FIELDS); this renders
// whatever it declares, so adding a connection kind is a data change, not a new
// screen. Supports the field types the add-version flow needs: text/url, select
// (native), and secret (reveal toggle). Conditional visibility keys off sibling
// values, matching the designer's dependsOn contract.
export default function DynamicField({ field, value, onChange, values }) {
  const [reveal, setReveal] = useState(false);

  if (field.dependsOn) {
    const dep = values?.[field.dependsOn.key];
    if (field.dependsOn.not != null && dep === field.dependsOn.not) return null;
    if (field.dependsOn.eq != null && dep !== field.dependsOn.eq) return null;
  }

  const label = (
    <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mb: 0.625 }}>
      <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{field.label}</Typography>
      {field.required && <Box component="span" sx={{ color: "error.main", typography: "s2" }}>*</Box>}
    </Stack>
  );

  const help = field.help && (
    <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.625 }}>{field.help}</Typography>
  );

  if (field.type === FIELD.SELECT) {
    return (
      <Box>
        {label}
        <TextField
          select
          fullWidth
          size="small"
          SelectProps={{ native: true }}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          sx={{ "& .MuiInputBase-root": { typography: "s2" } }}
        >
          <option value="">Select…</option>
          {field.options.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </TextField>
        {help}
      </Box>
    );
  }

  if (field.type === FIELD.SECRET) {
    return (
      <Box>
        {label}
        <TextField
          fullWidth size="small"
          type={reveal ? "text" : "password"}
          value={value ?? ""}
          placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)}
          InputProps={{
            endAdornment: (
              <InputAdornment position="end">
                <IconButton
                  size="small"
                  aria-label={reveal ? "Hide value" : "Reveal value"}
                  onClick={() => setReveal((r) => !r)}
                >
                  <Iconify
                    icon={reveal ? "solar:eye-closed-bold" : "solar:eye-bold"}
                    width={15}
                    sx={{ color: "text.subtitle" }}
                  />
                </IconButton>
              </InputAdornment>
            ),
          }}
          sx={{ "& .MuiInputBase-root": { typography: "s2" } }}
        />
        {help}
      </Box>
    );
  }

  return (
    <Box>
      {label}
      <TextField
        fullWidth size="small"
        value={value ?? ""}
        placeholder={field.placeholder}
        onChange={(e) => onChange(e.target.value)}
        sx={{
          "& .MuiInputBase-root": {
            typography: "s2",
            ...(field.type === FIELD.URL && {
              fontFamily: "ui-monospace, Menlo, monospace",
            }),
          },
        }}
      />
      {help}
    </Box>
  );
}

DynamicField.propTypes = {
  field: PropTypes.shape({
    key: PropTypes.string,
    label: PropTypes.string,
    type: PropTypes.string,
    required: PropTypes.bool,
    placeholder: PropTypes.string,
    help: PropTypes.string,
    options: PropTypes.arrayOf(PropTypes.shape({
      value: PropTypes.string,
      label: PropTypes.string,
    })),
    dependsOn: PropTypes.shape({
      key: PropTypes.string,
      eq: PropTypes.any,
      not: PropTypes.any,
    }),
  }).isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  values: PropTypes.objectOf(PropTypes.any),
};
