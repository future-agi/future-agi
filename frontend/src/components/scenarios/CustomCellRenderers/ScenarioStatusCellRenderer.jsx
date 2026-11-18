import React from "react";
import PropTypes from "prop-types";
import { Chip } from "@mui/material";
import {
  isScenarioCompleted,
  isScenarioFailed,
  isScenarioInProgress,
} from "src/utils/scenarioStatus";

const ScenarioStatusChip = ({ status }) => {
  let config;
  if (isScenarioInProgress(status)) {
    config = { label: "Processing", color: "warning" };
  } else if (isScenarioCompleted(status)) {
    config = { label: "Completed", color: "success" };
  } else if (isScenarioFailed(status)) {
    config = { label: "Failed", color: "error" };
  }
  if (!config) return null;

  return (
    <Chip
      size="small"
      variant="outlined"
      label={config.label}
      color={config.color}
    />
  );
};

ScenarioStatusChip.propTypes = {
  status: PropTypes.string,
};

export default ScenarioStatusChip;
