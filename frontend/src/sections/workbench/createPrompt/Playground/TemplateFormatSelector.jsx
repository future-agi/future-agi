import React, { useState } from "react";
import { Box, MenuItem, Popover, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";

const TEMPLATE_FORMATS = [
  {
    value: "mustache",
    label: "Mustache",
    icon: "{{x}}",
    description: "{{variable}}, {{obj.key}}",
  },
  {
    value: "jinja",
    label: "Jinja",
    icon: "{% %}",
    description: "{{ variable }}, {% if %}, {% for %}",
  },
];

const TemplateFormatSelector = ({ value, onChange, disabled }) => {
  const [anchorEl, setAnchorEl] = useState(null);
  const open = Boolean(anchorEl);
  const selected = TEMPLATE_FORMATS.find((f) => f.value === value);

  return (
    <>
      <Box
        onClick={(e) => !disabled && setAnchorEl(e.currentTarget)}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          padding: "4px 10px",
          border: "1px solid",
          borderColor: "border.default",
          borderRadius: "4px",
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.5 : 1,
          bgcolor: open ? "action.hover" : "transparent",
          flexShrink: 0,
          "&:hover": {
            bgcolor: disabled ? "transparent" : "action.hover",
          },
        }}
      >
        <Typography
          sx={{
            fontSize: "13px",
            fontWeight: 700,
            fontFamily: "monospace",
            color: "text.secondary",
            lineHeight: 1,
          }}
        >
          {selected?.icon || "{{x}}"}
        </Typography>
        <Typography
          variant="s2"
          fontWeight="fontWeightMedium"
          color="text.primary"
          sx={{ whiteSpace: "nowrap" }}
        >
          {selected?.label || "Mustache"}
        </Typography>
        <Iconify
          icon="eva:chevron-down-fill"
          width={16}
          height={16}
          sx={{
            color: "text.secondary",
            transform: open ? "rotate(180deg)" : "none",
            transition: "transform 0.2s",
          }}
        />
      </Box>
      <Popover
        open={open}
        anchorEl={anchorEl}
        onClose={() => setAnchorEl(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: { borderRadius: "8px", p: 0.5, minWidth: 220, mt: 0.5 },
          },
        }}
      >
        {TEMPLATE_FORMATS.map((fmt) => (
          <MenuItem
            key={fmt.value}
            selected={value === fmt.value}
            onClick={() => {
              onChange(fmt.value);
              setAnchorEl(null);
            }}
            sx={{ borderRadius: "6px", py: 1, gap: 1.5 }}
          >
            <Typography
              sx={{
                fontSize: "14px",
                fontWeight: 700,
                fontFamily: "monospace",
                width: 40,
                textAlign: "center",
                color: value === fmt.value ? "primary.main" : "text.secondary",
              }}
            >
              {fmt.icon}
            </Typography>
            <Box>
              <Typography
                variant="body2"
                sx={{ fontSize: "13px", fontWeight: 600 }}
              >
                {fmt.label}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontSize: "11px" }}
              >
                {fmt.description}
              </Typography>
            </Box>
          </MenuItem>
        ))}
      </Popover>
    </>
  );
};

TemplateFormatSelector.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

export default TemplateFormatSelector;
