import React, { useState } from "react";
import {
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Checkbox,
  ListItemText,
  OutlinedInput,
  Box,
} from "@mui/material";
import { Controller } from "react-hook-form";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import PropTypes from "prop-types";

const MultiSelectWithCheckboxes = ({
  control,
  fieldName,
  options,
  onChange,
  label = "Annotation Value",
  placeholder = "Select Choices",
  size = "small",
  ...props
}) => {
  const [open, setOpen] = useState(false);
  return (
    <Controller
      control={control}
      name={fieldName}
      render={({ field }) => {
        const safeValue = Array.isArray(field.value) ? field.value : [];

        const handleChange = (event) => {
          const selectedValues = event.target.value;
          field.onChange(selectedValues);
          if (onChange) {
            onChange({ target: { value: selectedValues } });
          }
        };

        return (
          <FormControl
            fullWidth
            size={size}
            sx={{ minWidth: "200px", ...props.sx }}
          >
            <InputLabel id={`${fieldName}-label`}>{label}</InputLabel>
            <Select
              {...field}
              labelId={`${fieldName}-label`}
              value={safeValue}
              multiple
              displayEmpty
              open={open}
              onOpen={() => setOpen(true)}
              onClose={() => setOpen(false)}
              onChange={handleChange}
              input={<OutlinedInput label={label} />}
              renderValue={(selected) => {
                if (!selected || selected.length === 0) {
                  return (
                    <span style={{ color: "var(--text-disabled)" }}>
                      {placeholder}
                    </span>
                  );
                }
                return (
                  <Box display="flex" alignItems="center">
                    {selected.join(", ")}
                  </Box>
                );
              }}
              sx={{
                height: size === "small" ? "41px" : "60px",
                "& .MuiSelect-select": {
                  display: "flex",
                  alignItems: "center",
                  minHeight: "auto !important",
                },
                ".MuiSelect-icon": {
                  color: "text.primary",
                  width: 22,
                  height: 22,
                },
                "& .MuiOutlinedInput-notchedOutline": {
                  borderRadius: open ? "4px 4px 0 0" : "4px",
                },
                "&:hover .MuiOutlinedInput-notchedOutline": {
                  borderColor: "divider",
                },
                "&.Mui-focused .MuiOutlinedInput-notchedOutline": {
                  borderColor: "divider",
                },
              }}
              MenuProps={{
                PaperProps: {
                  sx: {
                    maxHeight: "300px",
                    border: "1px solid",
                    borderColor: "divider",
                    borderTop: "none",
                    borderRadius: "0 0 4px 4px !important",
                  },
                },
              }}
            >
              {options.map((option) => (
                <MenuItem
                  key={option.value}
                  value={option.value}
                  sx={{
                    // fontweight: 400,
                    display: "flex",
                    alignItems: "center",
                    "&:hover": {
                      backgroundColor: "background.default",
                    },
                    "&.Mui-selected": {
                      backgroundColor: "background.neutral",
                    },
                  }}
                >
                  <Checkbox
                    checked={safeValue.includes(option.value)}
                    size="small"
                  />
                  <ListItemText primary={option.label} />
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        );
      }}
    />
  );
};

MultiSelectWithCheckboxes.propTypes = {
  control: PropTypes.object.isRequired,
  fieldName: PropTypes.string.isRequired,
  options: PropTypes.array.isRequired,
  onChange: PropTypes.func,
  label: PropTypes.string,
  placeholder: PropTypes.string,
  size: PropTypes.oneOf(["small", "medium", "large"]),
  sx: PropTypes.object,
};

const CategoricalAnnotationSelectField = ({
  control,
  fieldName,
  options,
  onChange,
  multiple = false,
  ...props
}) => {
  if (multiple) {
    return (
      <MultiSelectWithCheckboxes
        control={control}
        fieldName={fieldName}
        options={options}
        onChange={onChange}
        {...props}
      />
    );
  }

  return (
    <FormSearchSelectFieldControl
      control={control}
      fieldName={fieldName}
      options={options}
      label="Annotation Value"
      placeholder="Select value"
      onChange={onChange}
      searchable={true}
      {...props}
    />
  );
};

CategoricalAnnotationSelectField.propTypes = {
  control: PropTypes.object,
  fieldName: PropTypes.string,
  onChange: PropTypes.func,
  options: PropTypes.array,
  multiple: PropTypes.bool,
};

export default CategoricalAnnotationSelectField;
