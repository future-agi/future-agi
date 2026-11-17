import React from "react";
import PropTypes from "prop-types";
import { Box, MenuItem, TextField } from "@mui/material";

/**
 * HO D3: the value of a Collaboration field (Assigned to, Comments,
 * Language): the values the server answers, shown by their labels.
 */
export default function CollabValueSelect({ value, onChange, property }) {
  const current = Array.isArray(value) ? value[0] ?? "" : value ?? "";
  const options = property?.enumValues ?? [];
  return (
    <Box sx={{ flex: 1, minWidth: 160, maxWidth: 260 }}>
      <TextField
        select
        size="small"
        fullWidth
        value={options.includes(current) ? current : ""}
        onChange={(e) => onChange(e.target.value)}
        SelectProps={{
          displayEmpty: true,
          renderValue: (v) =>
            v ? property?.enumLabels?.[v] ?? v : "Choose a value",
        }}
        inputProps={{
          "aria-label": `${property?.name ?? "Field"} value`,
          "data-testid": `collab-value-${property?.id ?? ""}`,
        }}
        sx={{ "& .MuiInputBase-root": { fontSize: 12, height: 28 } }}
      >
        {options.map((v) => (
          <MenuItem key={v} value={v} sx={{ fontSize: 12 }}>
            {property?.enumLabels?.[v] ?? v}
          </MenuItem>
        ))}
      </TextField>
    </Box>
  );
}

CollabValueSelect.propTypes = {
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  property: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    enumValues: PropTypes.array,
    enumLabels: PropTypes.object,
  }),
};
