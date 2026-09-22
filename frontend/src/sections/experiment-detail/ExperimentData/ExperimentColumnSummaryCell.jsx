import { Box, Menu, MenuItem, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import {
  formatColumnSummary,
  getAvailableColumnSummaryTypes,
  getColumnSummaryLabel,
  resolveColumnSummaryType,
} from "./columnSummary";
import { useColumnSummaryStoreShallow } from "./states";

const ExperimentColumnSummaryCell = (props) => {
  const field = props?.colDef?.field || props?.column?.colId;
  const stats = props?.data?.[field];
  const summaryType = useColumnSummaryStoreShallow(
    (state) => state.summaryByColumn[field] || "average",
  );
  const setColumnSummary = useColumnSummaryStoreShallow(
    (state) => state.setColumnSummary,
  );
  const [anchorEl, setAnchorEl] = useState(null);

  const availableTypes = getAvailableColumnSummaryTypes(stats);
  const display = formatColumnSummary(stats, summaryType);
  const resolvedType = resolveColumnSummaryType(stats, summaryType);
  const interactive = availableTypes.length > 1;

  if (!stats?.isColumnSummary || !display) {
    return null;
  }

  const handleOpen = (event) => {
    event.stopPropagation();
    if (!interactive) return;
    setAnchorEl(event.currentTarget);
  };

  const handleClose = (event) => {
    event?.stopPropagation?.();
    setAnchorEl(null);
  };

  const handleSelect = (event, type) => {
    event.stopPropagation();
    setColumnSummary(field, type);
    setAnchorEl(null);
  };

  return (
    <Box
      sx={{
        padding: 1,
        lineHeight: 1.5,
        display: "flex",
        alignItems: "center",
        height: "100%",
      }}
    >
      <Box
        component={interactive ? "button" : "span"}
        type={interactive ? "button" : undefined}
        onClick={handleOpen}
        aria-haspopup={interactive ? "menu" : undefined}
        aria-expanded={interactive ? Boolean(anchorEl) : undefined}
        aria-label={
          interactive ? `Change column summary, currently ${display}` : display
        }
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          border: 0,
          background: "none",
          padding: 0,
          margin: 0,
          cursor: interactive ? "pointer" : "default",
          color: "text.primary",
          font: "inherit",
        }}
      >
        <Typography component="span" sx={{ fontSize: "13px", lineHeight: 1.5 }}>
          {display}
        </Typography>
        {interactive ? (
          <Iconify
            icon="mdi:chevron-down"
            width={14}
            height={14}
            sx={{ color: "text.secondary" }}
          />
        ) : null}
      </Box>
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={handleClose}
        anchorOrigin={{ vertical: "top", horizontal: "left" }}
        transformOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: {
              borderRadius: 1,
              minWidth: 140,
            },
          },
        }}
      >
        {availableTypes.map((item) => (
          <MenuItem
            key={item.id}
            selected={item.id === resolvedType}
            onClick={(event) => handleSelect(event, item.id)}
            sx={{ typography: "s1", fontWeight: "fontWeightRegular" }}
          >
            {getColumnSummaryLabel(item.id)}
          </MenuItem>
        ))}
      </Menu>
    </Box>
  );
};

ExperimentColumnSummaryCell.propTypes = {
  colDef: PropTypes.object,
  column: PropTypes.object,
  data: PropTypes.object,
};

export default ExperimentColumnSummaryCell;
