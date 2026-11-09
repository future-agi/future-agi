import { Box, Skeleton } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, { useMemo, useRef } from "react";
import { ExperimentSummaryDefaultColDef } from "./tableConfig";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import PropTypes from "prop-types";
import ChooseWinnerDrawer from "./ChooseWinnerDrawer";
import { useExperimentDetailContext } from "../experiment-context";
import ExperimentBarSummaryRightSection from "../ExperimentBarRightSection/ExperimentBarSummaryRightSection";

const SUMMARY_TABLE_THEME_PARAMS = {
  columnBorder: false,
  rowVerticalPaddingScale: 1,
  headerColumnBorder: { width: 0 },
  wrapperBorder: { width: 0 },
  wrapperBorderRadius: 0,
};

const SkeletonCellRenderer = () => (
  <Box
    sx={{
      display: "flex",
      alignItems: "center",
      height: "100%",
    }}
  >
    <Skeleton
      variant="rectangular"
      width="80%"
      height={20}
      sx={{ borderRadius: 0.5 }}
    />
  </Box>
);

// The "All runs" grid of the Summary (PRODUCT-SPEC §3.13 M8.2). The toolbar
// and the Choose winner drawer moved to ExperimentAnalysisView so the same
// grid serves the experiment and the compare Summary.
// Callers that still pass `evalsList` (the individual-experiment Summary)
// keep the old toolbar and Choose winner drawer above the grid.
function LegacyToolbar({ evalsList, experimentId }) {
  const { chooseWinnerOpen, setChooseWinnerOpen } =
    useExperimentDetailContext();
  return (
    <>
      <Box display="flex" justifyContent={"flex-end"} mb={1} mt={-1}>
        <ExperimentBarSummaryRightSection />
      </Box>
      <ChooseWinnerDrawer
        open={chooseWinnerOpen}
        onClose={() => setChooseWinnerOpen(false)}
        evalsList={evalsList}
        experimentId={experimentId}
      />
    </>
  );
}

LegacyToolbar.propTypes = {
  evalsList: PropTypes.array,
  experimentId: PropTypes.string,
};

const ExperimentSummaryTable = ({
  columns,
  rows,
  loading,
  getRowId,
  evalsList,
  legacyExperimentId,
  gridRef,
  gridProps,
}) => {
  const agTheme = useAgThemeWith(SUMMARY_TABLE_THEME_PARAMS);
  const ownRef = useRef(null);
  const gridApiRef = gridRef ?? ownRef;

  const skeletonColumns = useMemo(() => {
    if (!loading || !columns?.length) return null;
    return columns.map((col) => ({
      ...col,
      cellRenderer: SkeletonCellRenderer,
    }));
  }, [columns, loading]);

  const skeletonRows = useMemo(() => {
    if (!loading) return null;
    const count = rows?.length || 3;
    return Array.from({ length: count }, (_, i) => ({ id: `skeleton-${i}` }));
  }, [loading, rows?.length]);

  return (
    <Box
      sx={{
        width: "100%",
        "& .ag-center-cols-viewport, & .ag-center-cols-container": {
          minHeight: "unset !important",
        },
      }}
    >
      {evalsList !== undefined && (
        <LegacyToolbar
          evalsList={evalsList}
          experimentId={legacyExperimentId}
        />
      )}
      <AgGridReact
        domLayout="autoHeight"
        ref={gridApiRef}
        columnDefs={loading ? skeletonColumns : columns}
        defaultColDef={ExperimentSummaryDefaultColDef}
        rowData={loading ? skeletonRows : rows}
        getRowId={loading ? undefined : getRowId}
        suppressRowClickSelection={true}
        suppressColumnVirtualisation={true}
        suppressCellFocus={true}
        suppressNoRowsOverlay={true}
        paginationPageSizeSelector={false}
        pagination={false}
        theme={agTheme}
        {...gridProps}
      />
    </Box>
  );
};

ExperimentSummaryTable.propTypes = {
  columns: PropTypes.array,
  rows: PropTypes.array,
  loading: PropTypes.bool,
  getRowId: PropTypes.func,
  evalsList: PropTypes.array,
  // Only the legacy toolbar consumes this ID; DSX analysis is unchanged.
  legacyExperimentId: PropTypes.string,
  // One view: the caller's grid ref and extra AgGridReact props (column
  // shaping from useAgColumnShaping, rowHeight).
  gridRef: PropTypes.object,
  gridProps: PropTypes.object,
};

export default ExperimentSummaryTable;
