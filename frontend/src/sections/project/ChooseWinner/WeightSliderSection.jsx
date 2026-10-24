import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const WeightSliderSection = ({ title, children, listSx }) => {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <Typography fontWeight={600} fontSize="13px" color="text.primary">
        {title}
      </Typography>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: "20px",
          ...listSx,
        }}
      >
        {children}
      </Box>
    </Box>
  );
};

WeightSliderSection.propTypes = {
  title: PropTypes.string,
  children: PropTypes.any,
  listSx: PropTypes.object,
};

export default WeightSliderSection;
