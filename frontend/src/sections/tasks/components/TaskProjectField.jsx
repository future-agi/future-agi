import React, { useState } from "react";
import PropTypes from "prop-types";
import { Autocomplete, TextField } from "@mui/material";
import { useController } from "react-hook-form";

// Keep the task's required Project picker on MUI's combobox/listbox keyboard
// path without changing the shared search-select primitive's other consumers.
export default function TaskProjectField({ control, options, onChange }) {
  const { field, fieldState } = useController({ control, name: "project" });
  const [open, setOpen] = useState(false);
  const selected = options.find((option) => option.value === field.value);
  return (
    <Autocomplete
      fullWidth
      size="small"
      options={options}
      value={
        selected ||
        (field.value ? { value: field.value, label: field.value } : null)
      }
      isOptionEqualToValue={(option, value) => option.value === value.value}
      getOptionLabel={(option) => option.label}
      noOptionsText="No projects available"
      open={open}
      onOpen={() => setOpen(true)}
      onClose={() => setOpen(false)}
      onBlur={field.onBlur}
      onKeyDown={(event) => {
        if (event.key === "Enter" && !open) {
          event.preventDefault();
          setOpen(true);
        }
      }}
      onChange={(_, option) => {
        const value = option?.value || "";
        onChange(value);
        field.onChange(value);
      }}
      renderInput={(params) => (
        <TextField
          {...params}
          name={field.name}
          inputRef={field.ref}
          label="Project"
          required
          error={Boolean(fieldState.error)}
          helperText={fieldState.error?.message}
          InputLabelProps={{ ...params.InputLabelProps, shrink: true }}
        />
      )}
    />
  );
}

TaskProjectField.propTypes = {
  control: PropTypes.object.isRequired,
  options: PropTypes.arrayOf(
    PropTypes.shape({
      value: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
    }),
  ).isRequired,
  onChange: PropTypes.func.isRequired,
};
