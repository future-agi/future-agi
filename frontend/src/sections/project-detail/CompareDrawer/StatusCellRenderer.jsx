import { Box, Chip } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const StatusCellRenderer = ({ value }) => {
  let color;

  if (value >= 0 && value <= 49) {
    color = "error";
  } else if (value >= 50 && value <= 79) {
    color = "warning";
  } else if (value >= 80 && value <= 100) {
    color = "success";
  }

  return (
    <Box>
      <Chip
        variant="soft"
        label={value ? value + "%" : value == null ? "Error" : value + "%"}
        size="small"
        color={color}
        sx={{
          paddingX: "4px",
        }}
      />
    </Box>
  );
};

StatusCellRenderer.propTypes = {
  value: PropTypes.any,
};

export default StatusCellRenderer;
