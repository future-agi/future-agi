import PropTypes from "prop-types";
import { Chip } from "@mui/material";

const SOURCE_CONFIG = {
  dataset_row: { label: "Dataset Row", color: "primary" },
  trace: { label: "Trace", color: "secondary" },
  observation_span: { label: "Span", color: "info" },
  trace_session: { label: "Session", color: "secondary" },
  prototype_run: { label: "Prototype", color: "success" },
  call_execution: { label: "Simulation", color: "warning" },
};

export default function SourceBadge({ sourceType }) {
  const config = SOURCE_CONFIG[sourceType] || {
    label: sourceType,
    color: "default",
  };
  return (
    <Chip
      label={config.label}
      color={config.color}
      size="small"
      variant="soft"
    />
  );
}

SourceBadge.propTypes = {
  sourceType: PropTypes.string,
};
