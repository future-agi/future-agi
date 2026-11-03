import { Box, Skeleton } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, { useMemo, useRef } from "react";
import { ExperimentSummaryDefaultColDef } from "./tableConfig";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import PropTypes from "prop-types";
import ChooseWinnerDrawer from "./ChooseWinnerDrawer";
import { useExperimentDetailContext } from "../experiment-context";
import ExperimentBarSummaryRightSection from "../ExperimentBarRightSection/ExperimentBarSummaryRightSection";
// import TableFilterOptions from "src/components/TableFilterOptions/TableFilterOptions";

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
      sx={{ borderRadius: "4px" }}
    />
  </Box>
);

const ExperimentSummaryTable = ({ columns, rows, evalsList, loading }) => {
  const agTheme = useAgThemeWith(SUMMARY_TABLE_THEME_PARAMS);
  const gridApiRef = useRef(null);
  // const [searchQuery, setSearchQuery] = useState("");

  const { chooseWinnerOpen, setChooseWinnerOpen } =
    useExperimentDetailContext();

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
    <Box>
      <Box display="flex" justifyContent={"flex-end"} mb={1} mt={-1}>
        {/* <TableFilterOptions
          gridApiRef={gridApiRef}
          resizerRef={resizerRef}
          columnConfigureRef={columnConfigureRef}
          setDevelopFilterOpen={setDevelopFilterOpen}
          setOpenColumnConfigure={setOpenColumnConfigure}
          setCellHeight={setCellHeight}
          setSearchQuery={setSearchQuery}
          isFilterApplied={isFilterApplied}
          isData={true}
          hideFilter
          hideColumnView
          hideSearch
          hideRowHeight
        /> */}
        <ExperimentBarSummaryRightSection />
      </Box>
      <ChooseWinnerDrawer
        open={chooseWinnerOpen}
        onClose={() => setChooseWinnerOpen(false)}
        evalsList={evalsList}
      />
      <Box sx={{ width: "100%" }}>
        <AgGridReact
          domLayout="autoHeight"
          ref={gridApiRef}
          columnDefs={loading ? skeletonColumns : columns}
          defaultColDef={ExperimentSummaryDefaultColDef}
          rowData={loading ? skeletonRows : rows}
          suppressRowClickSelection={true}
          suppressColumnVirtualisation={true}
          paginationPageSizeSelector={false}
          pagination={false}
          theme={agTheme}
        />
      </Box>
    </Box>
  );
};

ExperimentSummaryTable.propTypes = {
  columns: PropTypes.array,
  rows: PropTypes.array,
  evalsList: PropTypes.array,
  loading: PropTypes.bool,
};

export default ExperimentSummaryTable;
