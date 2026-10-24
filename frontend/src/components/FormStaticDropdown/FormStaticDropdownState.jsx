import React, { useState } from "react";
import {
  FormControl,
  Select,
  MenuItem,
  Box,
  Typography,
  FormHelperText,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

const FormStaticDropdownState = React.forwardRef(
  (
    {
      options = [],
      value = "",
      onChange = (_val) => {},
      placeholder = "",
      fullWidth = true,
      disabled = false,
      size = "medium",
      sx = {},
      variant = "outlined",
      isError = false,
      errorMessage = "",
      helperText = "",
      ...rest
    },
    ref,
  ) => {
    const [open, setOpen] = useState(false);

    const handleChange = (event) => {
      onChange?.(event.target.value);
    };

    const handleOpen = () => {
      setOpen(true);
    };

    const handleClose = () => {
      setOpen(false);
    };

    return (
      <FormControl fullWidth={fullWidth} disabled={disabled} sx={sx}>
        <Select
          ref={ref}
          value={value}
          onChange={handleChange}
          onOpen={handleOpen}
          onClose={handleClose}
          open={open}
          // @ts-ignore
          size={size}
          // @ts-ignore
          variant={variant}
          displayEmpty
          IconComponent={() => (
            <Box
              onClick={open ? handleClose : handleOpen}
              sx={{
                marginRight: 1,
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                transform: open ? "rotate(180deg)" : "rotate(0deg)",
                transition: "transform 0.2s ease-in-out",
              }}
            >
              <Iconify
                // @ts-ignore
                icon="solar:alt-arrow-down-linear"
                width={20}
                height={20}
                sx={{ color: "text.disabled" }}
              />
            </Box>
          )}
          sx={{
            borderRadius: "8px !important",
            "& .MuiSelect-select .MuiTypography-root": {
              lineHeight: "22px",
              typography: "s1",
              fontWeight: "fontWeightMedium",
              color: "text.secondary",
            },
          }}
          MenuProps={{
            PaperProps: {
              sx: {
                borderRadius: "8px",
                marginTop: "4px",
              },
            },
          }}
          {...rest}
        >
          {!value && placeholder && (
            <MenuItem disabled value="">
              <Typography
                typography="s1"
                fontWeight={400}
                color="text.disabled"
              >
                {placeholder}
              </Typography>
            </MenuItem>
          )}

          {options.map((option) => {
            const { label, value, ...menuItemProps } = option;
            return (
              <MenuItem key={value} value={value} {...menuItemProps}>
                <Typography
                  typography="s3"
                  fontWeight="fontWeightRegular"
                  color="text.primary"
                >
                  {label}
                </Typography>
              </MenuItem>
            );
          })}
        </Select>
        {(isError || helperText) && (
          <FormHelperText
            sx={{
              marginLeft: 0,
              color: isError ? "status.danger.fg" : "text.primary",
            }}
          >
            {errorMessage || helperText}
          </FormHelperText>
        )}
      </FormControl>
    );
  },
);

FormStaticDropdownState.displayName = "FormStaticDropdownState";

FormStaticDropdownState.propTypes = {
  options: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.oneOfType([PropTypes.string, PropTypes.number])
        .isRequired,
    }),
  ).isRequired,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onChange: PropTypes.func,
  placeholder: PropTypes.string,
  fullWidth: PropTypes.bool,
  disabled: PropTypes.bool,
  size: PropTypes.oneOf(["small", "medium"]),
  sx: PropTypes.object,
  variant: PropTypes.oneOf(["outlined", "filled", "standard"]),
  helperText: PropTypes.string,
  errorMessage: PropTypes.string,
  isError: PropTypes.bool,
};

export default FormStaticDropdownState;
