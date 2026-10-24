import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import StatusBadge from "src/components/status-badge/StatusBadge";

const statusRole = {
  NotStarted: "neutral",
  Running: "info",
  Completed: "success",
  Editing: "info",
  Inactive: "neutral",
  Failed: "danger",
};

const AnnotationsCellRenderer = ({ value }) => {
  return (
    <Box>
      <StatusBadge status={value} label={value} role={statusRole[value]} />
    </Box>
  );
};

AnnotationsCellRenderer.propTypes = {
  value: PropTypes.string,
};

export default AnnotationsCellRenderer;
