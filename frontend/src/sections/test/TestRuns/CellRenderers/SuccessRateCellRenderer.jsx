import { Box, LinearProgress } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";

const SuccessRateCellRenderer = ({ value }) => {
  const percent = Math.min(100, Math.max(0, value));

  // Gray and target green
  const gray = { r: 242, g: 242, b: 242 };
  const green = { r: 0, g: 162, b: 81 };
  // Use easing so green starts earlier
  const eased = Math.pow(percent / 100, 0.5); // √ makes curve faster towards green
  const mix = (start, end) => Math.round(start + (end - start) * eased);

  const barColor = `rgb(${mix(gray.r, green.r)}, ${mix(gray.g, green.g)}, ${mix(gray.b, green.b)})`;
  const backgroundColor = `rgb(${gray.r}, ${gray.g}, ${gray.b})`;

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
      <Box>{percent}%</Box>
      <LinearProgress
        value={percent}
        variant="determinate"
        sx={{
          flexGrow: 1,
          height: "10px",
          borderRadius: "6px",
          backgroundColor: backgroundColor, // Set the track background
          "& .MuiLinearProgress-bar": {
            backgroundColor: barColor,
            borderRadius: "6px", // Match the container border radius
          },
          // Override any default MUI theming that might add purple tints
          "& .MuiLinearProgress-colorPrimary": {
            backgroundColor: backgroundColor,
          },
          "& .MuiLinearProgress-barColorPrimary": {
            backgroundColor: barColor,
          },
        }}
      />
    </Box>
  );
};

SuccessRateCellRenderer.propTypes = {
  value: PropTypes.number,
};

export default SuccessRateCellRenderer;
