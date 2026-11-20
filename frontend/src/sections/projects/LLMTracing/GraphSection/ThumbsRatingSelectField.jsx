import React, { useMemo, useState } from "react";
import { Box, FormControl, InputLabel, MenuItem, Select } from "@mui/material";
import Iconify from "src/components/iconify";
import { Controller } from "react-hook-form";
import PropTypes from "prop-types";

const ThumbsRatingSelectField = ({
  control,
  fieldName,
  onChange,
  label = "Class",
  placeholder = "Choose Class",
  size = "small",
  ...rest
}) => {
  const [open, setOpen] = useState(false);
  const thumbsOptions = useMemo(
    () => [
      {
        value: true,
        render: (
          <Box display="flex" alignItems="center" gap={1}>
            <Iconify
              icon="solar:like-linear"
              width={24}
              sx={{ color: "success.main" }}
            />
          </Box>
        ),
      },
      {
        value: false,
        render: (
          <Box display="flex" alignItems="center" gap={1}>
            <Iconify
              icon="solar:dislike-linear"
              width={24}
              sx={{ color: "error.main" }}
            />
          </Box>
        ),
      },
    ],
    [],
  );

  return (
    <FormControl size={size} sx={{ width: 200 }}>
      <InputLabel>{label}</InputLabel>
      <Controller
        control={control}
        name={fieldName}
        defaultValue={null}
        render={({ field }) => (
          <Select
            {...field}
            label={label}
            displayEmpty
            open={open}
            onOpen={() => setOpen(true)}
            onClose={() => setOpen(false)}
            onChange={(e) => {
              field.onChange(e);
              onChange?.(e);
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
              ...rest.sx,
            }}
            inputProps={{
              sx: {
                "& .MuiOutlinedInput-notchedOutline": {
                  borderColor: "divider",
                },
                "&:hover .MuiOutlinedInput-notchedOutline": {
                  borderColor: "divider",
                },
                "&.Mui-focused .MuiOutlinedInput-notchedOutline": {
                  borderColor: "text.primary",
                },
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
            renderValue={(selected) => {
              if (selected === null || selected === undefined) {
                return (
                  <span style={{ color: "var(--text-disabled)" }}>
                    {placeholder}
                  </span>
                );
              }
              return (
                <Box display="flex" alignItems="center" gap={1}>
                  <Iconify
                    icon={
                      selected ? "solar:like-linear" : "solar:dislike-linear"
                    }
                    width={24}
                    sx={{
                      color: selected ? "success.main" : "error.main",
                    }}
                  />
                </Box>
              );
            }}
            {...rest}
          >
            {thumbsOptions.map((option) => (
              <MenuItem
                key={option.value}
                value={option.value}
                sx={{
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
                {option.render}
              </MenuItem>
            ))}
          </Select>
        )}
      />
    </FormControl>
  );
};

ThumbsRatingSelectField.propTypes = {
  control: PropTypes.object.isRequired,
  fieldName: PropTypes.string.isRequired,
  onChange: PropTypes.func,
  placeholder: PropTypes.string,
  label: PropTypes.string,
  disabled: PropTypes.bool,
  size: PropTypes.string,
  fullWidth: PropTypes.bool,
  sx: PropTypes.object,
};

export default ThumbsRatingSelectField;
