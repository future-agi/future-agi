import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { normalizeEvalCellValue } from "src/sections/develop-detail/DataTab/common";

const StatusCellRenderer = ({ value }) => {
  const normalized = normalizeEvalCellValue(value);
  let displayValue = normalized;
  if (Array.isArray(normalized)) {
    displayValue = normalized[0];
  } else if (normalized && typeof normalized === "object") {
    displayValue =
      typeof normalized.score === "number"
        ? normalized.score * 100
        : normalized.choice ?? "";
  }

  // Score bands take a status role; text results ("Passed", "Failed") and
  // the empty "Error" case are inferred from their label.
  let role;
  if (displayValue >= 0 && displayValue <= 49) {
    role = "danger";
  } else if (displayValue >= 50 && displayValue <= 79) {
    role = "warning";
  } else if (displayValue >= 80 && displayValue <= 100) {
    role = "success";
  }

  const isNumeric = typeof displayValue === "number" && !isNaN(displayValue);
  const label = isNumeric
    ? `${displayValue}%`
    : displayValue == null || displayValue === ""
      ? "Error"
      : String(displayValue);

  return (
    <Box>
      <StatusBadge status={label} label={label} role={role} />
    </Box>
  );
};

StatusCellRenderer.propTypes = {
  value: PropTypes.any,
};

export default StatusCellRenderer;
