import React from "react";
import { Box, Chip, Skeleton, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import { getStatusColor, normalizeEvalResult } from "../common";
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
          color: theme.palette.error.main,
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

  const chipSx = (v) => ({
    ...getStatusColor(v, theme),
    transition: "none",
    "&:hover": {
      backgroundColor: getStatusColor(v, theme).backgroundColor,
      boxShadow: "none",
    },
  });

  if (result.kind === "score") {
    const pct = result.score <= 1 ? result.score * 100 : result.score;
    return (
      <Chip
        variant="soft"
        label={`${Math.round(pct)}%`}
        size="small"
        sx={chipSx(result.score)}
      />
    );
  }

  if (result.kind === "passfail") {
    return (
      <Chip
        variant="soft"
        label={result.label}
        size="small"
        sx={chipSx(result.label)}
      />
    );
  }

  // choices — show first chip plus a +N counter if more.
  const first = result.items[0];
  return (
    <Box>
      <Chip
        variant="soft"
        label={first}
        size="small"
        sx={{ ...chipSx(first), marginRight: "10px" }}
      />
      {result.items.length > 1 && (
        <Chip
          variant="soft"
          label={`+${result.items.length - 1}`}
          size="small"
          sx={chipSx(first)}
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
