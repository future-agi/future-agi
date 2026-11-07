import { useState } from "react";
import { Box, Slider, Typography } from "@mui/material";
import PropTypes from "prop-types";

const SlideNumber = ({
  label,
  min = 0,
  max = 100,
  step = 1,
  containerStyle = {},
}) => {
  const [sliderValue, setSliderValue] = useState((+min + +max) / 2);

  const handleSliderChange = (event, newValue) => {
    setSliderValue(newValue);
  };

  // Calculate the number of decimal places
  const getDecimalPlaces = (value) => {
    if (Math.floor(value) === value) return 0;
    return value.toString().split(".")[1].length || 0;
  };

  const decimalPlaces = Math.max(
    getDecimalPlaces(+step),
    getDecimalPlaces(+min),
    getDecimalPlaces(+max),
  );

  return (
    <Box
      sx={{
        width: "100%",
        display: "flex",
        gap: 3,
        paddingTop: "20px",
        ...containerStyle,
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography variant="body1" sx={{ fontWeight: "medium", fontSize: 13 }}>
          {label ? label + ": " : ""}
        </Typography>
      </Box>

      <Box width={"100%"}>
        {/* Slider */}
        <Slider
          value={sliderValue}
          onChange={(_, newValue) => handleSliderChange(_, newValue)}
          min={+min}
          max={+max}
          step={+step}
          aria-label={label}
          valueLabelDisplay="auto"
          sx={styles.circular}
          scale={(x) => Number.parseFloat(x.toFixed(decimalPlaces))}
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
            border: "1px solid var(--border-default)",
            padding: "5px 20px",
            borderRadius: "6px",
            fontSize: 13,
          }}
        >
          {sliderValue.toFixed(decimalPlaces)}
        </Typography>
      </Box>
    </Box>
  );
};

SlideNumber.propTypes = {
  label: PropTypes.string.isRequired, // Title for the slider
  min: PropTypes.oneOfType([PropTypes.number, PropTypes.string]), // Minimum value
  max: PropTypes.oneOfType([PropTypes.number, PropTypes.string]), // Maximum value
  step: PropTypes.oneOfType([PropTypes.number, PropTypes.string]), // Step size
  containerStyle: PropTypes.object,
};

export default SlideNumber;

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
