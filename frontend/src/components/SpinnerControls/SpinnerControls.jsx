import React from "react";
import { Box } from "@mui/material";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";

export const SpinnerControls = ({ value, onChange }) => {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        position: "absolute",
        right: "12px",
      }}
    >
      <Iconify
        icon="solar:alt-arrow-up-linear"
        sx={{
          cursor: "pointer",
          color: "text.primary",
          width: 16,
          height: 16,
          // '&:hover': {
          //   color: '#7857FC',
          // },
        }}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          const currentValue = parseInt(value) || 0;
          onChange(currentValue + 1);
        }}
      />
      <Iconify
        icon="solar:alt-arrow-down-linear"
        sx={{
          cursor: "pointer",
          color: "text.primary",
          width: 16,
          height: 16,
          // '&:hover': {
          //   color: '#7857FC',
          // },
        }}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          const currentValue = parseInt(value) || 0;
          if (currentValue > 1) {
            onChange(currentValue - 1);
          }
        }}
      />
    </Box>
  );
};

SpinnerControls.propTypes = {
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onChange: PropTypes.func.isRequired,
};
