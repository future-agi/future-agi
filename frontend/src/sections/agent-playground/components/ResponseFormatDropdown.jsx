import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Menu,
  MenuItem,
  Typography,
  useTheme,
  Divider,
  ButtonBase,
} from "@mui/material";
import { useController } from "react-hook-form";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";

export default function ResponseFormatDropdown({
  control,
  fieldName,
  options,
  disabled,
  onCreateSchema,
}) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const anchorRef = useRef(null);

  const { field } = useController({
    name: fieldName,
    control,
  });

  const selectedOption = options?.find((opt) => opt.value === field.value) ||
    options?.[0] || { label: "Text output", value: "text" };

  const handleOpen = () => {
    if (!disabled) {
      setOpen(true);
    }
  };

  const handleClose = () => {
    setOpen(false);
  };

  const handleSelect = (value) => {
    field.onChange(value);
    handleClose();
  };

  const handleCreateSchema = () => {
    handleClose();
    onCreateSchema?.();
  };

  return (
    <>
      <ButtonBase
        component="div"
        ref={anchorRef}
        onClick={handleOpen}
        sx={{
          display: "flex",
          justifyContent: "flex-start",
          textAlign: "left",
          alignItems: "center",
          gap: 1,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          padding: theme.spacing(0.75, 1.5),
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.5 : 1,
          transition: "all 0.2s ease-in-out",
          height: "32px",
          "&:hover": {
            borderColor: "divider",
            bgcolor: "background.paper",
          },
        }}
      >
        <Iconify
          icon="solar:text-field-linear"
          width={20}
          height={20}
          sx={{ color: "text.primary" }}
        />
        <Typography
          typography="s2_1"
          fontWeight="fontWeightMedium"
          color="text.primary"
          sx={{ whiteSpace: "nowrap" }}
        >
          {selectedOption.label}
        </Typography>
        <SvgColor
          src="/assets/icons/custom/lucide--chevron-down.svg"
          sx={{
            width: 16,
            height: 16,
            color: "text.primary",
            rotate: open ? "180deg" : "0deg",
            transition: "all 0.2s ease-in-out",
          }}
        />
      </ButtonBase>

      <Menu
        anchorEl={anchorRef.current}
        open={open}
        onClose={handleClose}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "left",
        }}
        transformOrigin={{
          vertical: "top",
          horizontal: "left",
        }}
        sx={{
          "& .MuiPopover-paper": {
            borderRadius: "4px",
            minWidth: anchorRef.current?.offsetWidth || 150,
            border: "1px solid",
            borderColor: "divider",
            mt: 0.5,
          },
        }}
      >
        <Box>
          <Box sx={{ maxHeight: "200px", overflowY: "auto" }}>
            {options?.map((option) => (
              <MenuItem
                key={option.value}
                selected={field.value === option.value}
                onClick={() => handleSelect(option.value)}
                sx={{
                  typography: "s1",
                  py: 1,
                  px: 1.5,
                  "&.Mui-selected": {
                    backgroundColor: "action.selected",
                  },
                  "&:hover": {
                    backgroundColor: "action.hover",
                  },
                }}
              >
                {option.label}
              </MenuItem>
            ))}
          </Box>
          {onCreateSchema && (
            <Box>
              <Divider sx={{ my: 0.5 }} />
              <MenuItem
                onClick={handleCreateSchema}
                sx={{
                  typography: "s1",
                  py: 1,
                  px: 1.5,
                  "&:hover": {
                    backgroundColor: "divider",
                  },
                }}
              >
                + Add custom schema
              </MenuItem>
            </Box>
          )}
        </Box>
      </Menu>
    </>
  );
}

ResponseFormatDropdown.propTypes = {
  control: PropTypes.any.isRequired,
  fieldName: PropTypes.string.isRequired,
  options: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.oneOfType([PropTypes.string, PropTypes.number])
        .isRequired,
    }),
  ).isRequired,
  disabled: PropTypes.bool,
  onCreateSchema: PropTypes.func,
};
