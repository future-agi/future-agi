import React from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Tooltip, Typography } from "@mui/material";

import SvgColor from "src/components/svg-color";

import { GatewayGrid } from "../../components/frame";
import { copyText } from "../copyText";
import { GATEWAY_MONO } from "../../utils/fonts";

// A bordered block of the model page, with an s1 medium title, an optional
// one-line note and actions on the right.
export function Section({ title, note, actions, children, sx }) {
  return (
    <Box
      component="section"
      aria-label={title}
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        minWidth: 0,
        ...sx,
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 1,
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography
            typography="s1"
            fontWeight="fontWeightMedium"
            component="h2"
          >
            {title}
          </Typography>
          {note && (
            <Typography typography="s2" color="text.secondary">
              {note}
            </Typography>
          )}
        </Box>
        {actions}
      </Box>
      {children}
    </Box>
  );
}

Section.propTypes = {
  title: PropTypes.string.isRequired,
  note: PropTypes.node,
  actions: PropTypes.node,
  children: PropTypes.node,
  sx: PropTypes.object,
};

export function CopyButton({ text, label = "Copy", message }) {
  return (
    <Tooltip title={label} arrow>
      <IconButton
        size="small"
        aria-label={label}
        onClick={(e) => {
          e.stopPropagation();
          copyText(text, message);
        }}
        sx={{ p: 0.5 }}
      >
        <SvgColor
          src="/assets/icons/ic_copy.svg"
          sx={{ width: 16, height: 16 }}
        />
      </IconButton>
    </Tooltip>
  );
}

CopyButton.propTypes = {
  text: PropTypes.string.isRequired,
  label: PropTypes.string,
  message: PropTypes.string,
};

export function Mono({ children, sx }) {
  return (
    <Typography
      component="span"
      typography="s2"
      sx={{ fontFamily: GATEWAY_MONO, overflowWrap: "anywhere", ...sx }}
    >
      {children}
    </Typography>
  );
}

Mono.propTypes = { children: PropTypes.node, sx: PropTypes.object };

// A table that scrolls sideways inside its section on a narrow screen
// instead of clipping its last column.
export function TableScroll({ children }) {
  return <Box sx={{ overflowX: "auto", minWidth: 0 }}>{children}</Box>;
}

TableScroll.propTypes = { children: PropTypes.node };

// Fact cells centre their content in the row; numbers sit on the right.
export const FACT_CELL = { display: "flex", alignItems: "center" };
export const FACT_NUMBER = {
  type: "rightAligned",
  cellStyle: { ...FACT_CELL, justifyContent: "flex-end" },
};
const FACT_COL_DEF = {
  sortable: false,
  flex: 1,
  minWidth: 96,
  cellStyle: FACT_CELL,
};

// AG Grid gives an auto-height body a 150 px minimum (room for its "no
// rows" overlay); a fact table always has rows and is as tall as they are.
const NO_MIN_BODY = {
  width: "100%",
  "& .ag-layout-auto-height .ag-center-cols-viewport, & .ag-layout-auto-height .ag-center-cols-container, & .ag-layout-auto-height .ag-center-cols-clipper":
    { minHeight: "0 !important" },
};

// A small fact table on the model page: a gateway grid (AG Grid on the
// Future AGI theme, as on run detail) sized to its rows, read-only.
export function FactGrid({ rowData, columnDefs, rowHeight = 36 }) {
  return (
    <Box sx={NO_MIN_BODY}>
      <GatewayGrid
        rowData={rowData}
        columnDefs={columnDefs}
        getRowId={(p) => p.data.key}
        defaultColDef={FACT_COL_DEF}
        domLayout="autoHeight"
        height="auto"
        headerHeight={36}
        rowHeight={rowHeight}
        suppressMovableColumns
      />
    </Box>
  );
}

FactGrid.propTypes = {
  rowData: PropTypes.array.isRequired,
  columnDefs: PropTypes.array.isRequired,
  rowHeight: PropTypes.number,
};

/** A grid cell in the page's text styles: s2, optionally secondary. */
export function FactText({ children, secondary, strong, sx }) {
  return (
    <Typography
      component="span"
      typography="s2"
      color={secondary ? "text.secondary" : "text.primary"}
      fontWeight={strong ? "fontWeightMedium" : undefined}
      sx={{ display: "block", ...sx }}
    >
      {children}
    </Typography>
  );
}

FactText.propTypes = {
  children: PropTypes.node,
  secondary: PropTypes.bool,
  strong: PropTypes.bool,
  sx: PropTypes.object,
};
