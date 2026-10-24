import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const ExperimentResponseTimeCellRenderer = ({ value }) => {
  return (
    <Box
      sx={{
        whiteSpace: "normal",
        wordBreak: "break-word",
        width: "100%",
        lineHeight: 1.5,
        alignItems: "center",
        display: "flex",
        height: "100%",
        gap: 1.5,
      }}
    >
      <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
        {/* <Iconify
          icon="solar:clock-circle-linear"
          sx={{ width: "14px", height: "14px", color: "text.primary" }}
        /> */}
        {value}
      </Box>
    </Box>
  );
};

export default ExperimentResponseTimeCellRenderer;

ExperimentResponseTimeCellRenderer.propTypes = {
  value: PropTypes.string,
};
