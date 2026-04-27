import { Box } from "@mui/material";
import React from "react";
import "./customMetric.css";
import Iconify from "src/components/iconify/iconify";
import CustomTooltip from "src/components/tooltip";
import PropTypes from "prop-types";

const CustomMetricActionButtons = ({ onEditClick }) => {
  return (
    <Box
      className="custom-metric-action-cell"
      sx={{ display: "flex", gap: 2, alignItems: "center" }}
      onClick={(e) => e.stopPropagation()}
    >
      <CustomTooltip title="Edit" placement="top" arrow>
        <Iconify
          onClick={() => onEditClick()}
          icon="solar:pen-bold"
          sx={{ color: "primary.main" }}
        />
      </CustomTooltip>
    </Box>
  );
};

CustomMetricActionButtons.propTypes = {
  onEditClick: PropTypes.func,
};

export default CustomMetricActionButtons;
