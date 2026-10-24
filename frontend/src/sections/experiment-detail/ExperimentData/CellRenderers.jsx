import React from "react";
import {
  getChipRole,
  getChipLabel,
  getChipTextColor,
  interpolateColorforExperiment,
  parseArrayString,
} from "./Common";
import { Box, Chip, Skeleton, Typography, useTheme } from "@mui/material";
import StatusBadge from "src/components/status-badge/StatusBadge";
import PropTypes from "prop-types";

export const StatusCellRenderer = ({ data, value }) => {
  // Use value from valueGetter if available, otherwise fall back to data.cellValue
  const cellData = value || data;

  const renderArray = () => {
    if (!cellData?.cellValue) {
      return (
        <Skeleton
          variant="rounded"
          width={80}
          height={24}
          sx={{ borderRadius: 1, margin: "5px" }}
        />
      );
    }

    try {
      const parsedData = parseArrayString(cellData.cellValue);
      if (Array.isArray(parsedData) && parsedData.length > 0) {
        const extra = parsedData.slice(0, 2).length - 1;
        return (
          <Typography
            variant="body2"
            noWrap
            sx={{ margin: "5px", color: "text.primary" }}
          >
            {parsedData[0]}
            {extra > 0 && (
              <Box
                component="span"
                sx={{
                  ml: 0.75,
                  color: "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {`+${extra}`}
              </Box>
            )}
          </Typography>
        );
      }
      return null;
    } catch (error) {
      return null;
    }
  };

  const isArray = cellData?.dataType === "array" || data?.dataType === "array";
  const isFloat = cellData?.dataType === "float" || data?.dataType === "float";
  const isError = cellData?.cellValue === "error";

  return (
    <Box>
      {isArray && !isError ? (
        renderArray()
      ) : isError ? (
        <Box sx={{ margin: "8px" }}>
          <StatusBadge status="error" label="error" />
        </Box>
      ) : isFloat ? (
        // A score, not a status: the tint follows the value.
        <Chip
          variant="soft"
          label={getChipLabel(cellData || data)}
          size="small"
          color="success"
          sx={{
            backgroundColor: cellData?.cellValue
              ? interpolateColorforExperiment(
                  parseFloat(cellData?.cellValue),
                  1,
                )
              : undefined,
            color: cellData?.cellValue
              ? getChipTextColor(parseFloat(cellData?.cellValue), 1)
              : undefined,
            pointerEvents: "none",
            margin: "5px",
          }}
        />
      ) : (
        <Box sx={{ margin: "5px", display: "inline-flex" }}>
          <StatusBadge
            status={getChipLabel(cellData || data)}
            label={getChipLabel(cellData || data)}
            role={getChipRole(cellData || data)}
          />
        </Box>
      )}
    </Box>
  );
};

export const ViewDetailCellRenderer = (params) => {
  const { data, setOpenDetailRow } = params;

  const theme = useTheme();

  const handleClick = () => {
    if (setOpenDetailRow && data) {
      setOpenDetailRow(data);
    }
  };

  return (
    <Box
      aria-label="open-view-detail"
      className="comparision-table-detail row-hover-action"
      onClick={handleClick}
      style={{
        color: theme.palette.primary.main,
        cursor: "pointer",
        pointerEvents: "auto",
        fontWeight: 400,
        fontSize: "13px",
      }}
    >
      View Detail
    </Box>
  );
};

StatusCellRenderer.propTypes = {
  data: PropTypes.object,
  value: PropTypes.object,
};

ViewDetailCellRenderer.propTypes = {
  data: PropTypes.object,
  setOpenDetailRow: PropTypes.func,
};
