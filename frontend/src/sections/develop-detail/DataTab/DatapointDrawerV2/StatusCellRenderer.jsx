import React from "react";
import { Box, Skeleton, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { statusColors } from "src/utils/runStatus";
import { evalResultRole, normalizeEvalResult } from "../common";
import NumericCell from "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/NumericCell";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";

const SkeletonLoader = () => (
  <Box
    sx={{
      paddingX: 1,
      display: "flex",
      alignItems: "center",
      height: "100%",
    }}
  >
    <Skeleton sx={{ width: "100%", height: "10px" }} variant="rounded" />
  </Box>
);

const StatusCellRenderer = ({ cellValue, status, isLoading, type }) => {
  const theme = useTheme();
  if (status === "running" || isLoading) return <SkeletonLoader />;
  if (status === "error") {
    return (
      <Box
        sx={{
          marginLeft: theme.spacing(1),
          color: statusColors(theme, "danger").fg,
          fontSize: "13px",
        }}
      >
        Error
      </Box>
    );
  }

  if (type === OutputTypes.NUMERIC) {
    return (
      <NumericCell
        value={cellValue}
        sx={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          paddingX: 1,
        }}
      />
    );
  }

  const result = normalizeEvalResult(cellValue, type);
  if (result.kind === "empty") return null;

  if (result.kind === "score") {
    const pct = result.score <= 1 ? result.score * 100 : result.score;
    return (
      <StatusBadge
        label={`${Math.round(pct)}%`}
        role={evalResultRole(result.score)}
        dot={false}
      />
    );
  }

  if (result.kind === "passfail") {
    return (
      <StatusBadge
        label={result.label}
        role={evalResultRole(result.label)}
        dot={false}
      />
    );
  }

  // choices — show first badge plus a +N counter if more.
  const first = result.items[0];
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: "10px" }}>
      <StatusBadge label={first} role={evalResultRole(first)} dot={false} />
      {result.items.length > 1 && (
        <StatusBadge
          label={`+${result.items.length - 1}`}
          role={evalResultRole(first)}
          dot={false}
        />
      )}
    </Box>
  );
};

StatusCellRenderer.propTypes = {
  cellValue: PropTypes.any,
  status: PropTypes.string,
  isLoading: PropTypes.bool,
  type: PropTypes.string,
};

export default StatusCellRenderer;
