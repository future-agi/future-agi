import React from "react";
import { Box, Chip } from "@mui/material";
import PropTypes from "prop-types";
const ScenarioTypeCellRenderer = ({ data }) => {
  const scenarioType =
    data.scenarioTypeDisplay || data.scenarioType || "Unknown";

  return (
    <Box height={"100%"} display={"flex"} alignItems={"center"}>
      <Chip
        label={scenarioType}
        size="small"
        variant="outlined" // make it outlined
        sx={{
          border: "1px solid var(--border-default)", // optional: default outlined border
          color: "text.primary", // text as black
          backgroundColor: "transparent", // no background
          "&:hover": {
            backgroundColor: "transparent", // no hover effect
          },
        }}
      />
    </Box>
  );
};

ScenarioTypeCellRenderer.propTypes = {
  data: PropTypes.object,
};

export default ScenarioTypeCellRenderer;
