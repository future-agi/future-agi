import { Box, LinearProgress, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const SummaryAverageCellRenderer = (props) => {
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
        height: "100%",
        display: "flex",
        alignItems: "center",
        gap: 2,
      }}
    >
      <LinearProgress
        color="success"
        value={props.value}
        variant="determinate"
        sx={{ width: "100%" }}
      />
      <Typography fontSize="13px" fontWeight={400} color="text.secondary">
        {props.value}%
      </Typography>
    </Box>
  );
};

SummaryAverageCellRenderer.propTypes = {
  value: PropTypes.number,
  column: PropTypes.object,
};

export default SummaryAverageCellRenderer;
