import React from "react";
import { Box, Slider, Typography } from "@mui/material";
import PropTypes from "prop-types";

const SlideNumberPreview = ({
  label,
  min = 0,
  max = 100,
  step,
  initialValue = 0,
  onValueChange = () => {},
  disabled = false,
}) => {
  const handleSliderChange = (event, value) => {
    if (Number.isNaN(value)) return;
    onValueChange(value);
  };

  return (
    <Box sx={{ width: "100%", display: "flex", gap: 3, paddingTop: "20px" }}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography
          variant="body1"
          sx={{ fontWeight: "medium", fontSize: 13, minWidth: "max-content" }}
        >
          {label}
        </Typography>
      </Box>

      <Box width={"100%"}>
        {/* Slider */}
        <Slider
          value={initialValue}
          onChange={handleSliderChange}
          min={min}
          max={max}
          step={step}
          aria-label={label}
          valueLabelDisplay="auto"
          sx={styles.circular}
          disabled={disabled}
        />

        {/* Min and Max Values */}
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            marginTop: -2,
          }}
        >
          <Typography variant="body2" color="textSecondary">
            {min}
          </Typography>
          <Typography variant="body2" color="textSecondary">
            {max}
          </Typography>
        </Box>
      </Box>
      <Box>
        <Typography
          variant="body1"
          sx={{
            fontWeight: "600",
            border: "1px solid var(--border-active)",
            padding: "5px 20px",
            borderRadius: "8px",
            fontSize: 13,
          }}
        >
          {initialValue}
        </Typography>
      </Box>
    </Box>
  );
};

SlideNumberPreview.propTypes = {
  label: PropTypes.string.isRequired,
  min: PropTypes.number, // Minimum value
  max: PropTypes.number, // Maximum value
  step: PropTypes.number, // Step size
  initialValue: PropTypes.number,
  onValueChange: PropTypes.func,
  disabled: PropTypes.bool,
};

export default SlideNumberPreview;

const styles = {
  circular: {
    height: 8,
    "& .MuiSlider-thumb": {
      width: 22,
      height: 22,
      backgroundColor: "action.hover",
      border: "3px solid var(--bg-paper)",
      "&:hover": {
        boxShadow: "none",
      },
      "&.Mui-active": {
        boxShadow: "none",
      },
    },
    "& .MuiSlider-track": {
      border: "none",
    },
    "& .MuiSlider-rail": {
      opacity: 0.5,
      backgroundColor: "action.hover",
    },
  },
};
