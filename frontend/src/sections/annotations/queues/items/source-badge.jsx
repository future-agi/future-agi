import PropTypes from "prop-types";
import { Chip } from "@mui/material";
import { alpha } from "@mui/material/styles";

const simulationSx = {
  color: (theme) =>
    theme.palette.mode === "light"
      ? theme.palette.orange[700]
      : theme.palette.orange[300],
  bgcolor: (theme) => alpha(theme.palette.orange[500], 0.16),
};

const SOURCE_CONFIG = {
  dataset_row: { label: "Dataset Row", color: "primary" },
  trace: { label: "Trace", color: "secondary" },
  observation_span: { label: "Span", color: "info" },
  prototype_run: { label: "Prototype", color: "success" },
  call_execution: { label: "Simulation", sx: simulationSx },
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
      sx={config.sx}
    />
  );
}

SourceBadge.propTypes = {
  sourceType: PropTypes.string,
};
