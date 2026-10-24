import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { interpolateColorBasedOnScore } from "src/utils/utils";

const SummaryPercentileCellRenderer = (props) => {
  const column = props?.column?.colDef;

  if (column?.field === "eval") {
    return (
      <Box
        sx={{
          padding: 1,
          display: "flex",
          alignItems: "center",
          height: "100%",
        }}
      >
        <Typography fontSize="13px" fontWeight={400} color="text.primary">
          {props.value}
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        padding: 1,
        backgroundColor: interpolateColorBasedOnScore(props.value, 100),
        height: "100%",
        display: "flex",
        alignItems: "center",
      }}
    >
      <Typography fontSize="13px" fontWeight={400} color="text.primary">
        {props.value}%
      </Typography>
    </Box>
  );
};

SummaryPercentileCellRenderer.propTypes = {
  column: PropTypes.object,
  value: PropTypes.any,
};

export default SummaryPercentileCellRenderer;
