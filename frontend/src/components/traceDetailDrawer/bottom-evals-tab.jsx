import React, { useMemo, useState, useRef } from "react";
import { Paper, Box, Skeleton } from "@mui/material";
import PropTypes from "prop-types";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import EvalsCustomCellRenderer from "./CustomRenderer/EvalsCustomCellRenderer";
import EvalCustomHeaderCellRenderer from "./CustomRenderer/EvalCustomHeaderCellRenderer";
import ViewDetailsModal from "./dialogue-boxes/view-details";
import AddFeedbackForm from "./dialogue-boxes/add-feedback-form";

const SkeletonCellRenderer = () => {
  return (
    <Skeleton
      variant="rectangular"
      width="40%"
      height={22}
      sx={{ marginTop: 2, marginLeft: 1 }}
    />
  );
};

const BOTTOM_EVALS_THEME_PARAMS = {
  columnBorder: true,
  rowVerticalPaddingScale: 0,
};

const BottomEvalsTab = ({
  observationSpan,
  isLoading,
  showAddFeedback = true,
  showViewDetail = true,
}) => {
  const agTheme = useAgThemeWith(BOTTOM_EVALS_THEME_PARAMS);
  const [showExplanationInColumn, setShowExplanationInColumn] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedAddFeedback, setSelectedAddFeedback] = useState(null);
  const [selectedViewDetail, setSelectedViewDetail] = useState(null);
  const gridRef = useRef(null);

  const cols = useMemo(() => {
    const baseCols = [
      {
        field: "name",
        headerIcon: "gg:check-o",
        headerName: "Evals",
        width: 258,
        minWidth: 258,
        maxWidth: 258,
        resizable: false,
        suppressSizeToFit: true,
        flex: 0,
      },
      {
        field: "score",
        headerIcon: "gg:check-o",
        headerLabel: "Score",
        flex: 1,
        minWidth: 150,
      },
    ];

    return baseCols.map((col) => ({
      ...col,
      cellRenderer: isLoading ? SkeletonCellRenderer : EvalsCustomCellRenderer,
    }));
  }, [isLoading]);

  const rows = useMemo(() => {
    if (isLoading) {
      return Array.from({ length: 5 }).map((_, idx) => ({
        id: `skeleton-${idx}`,
        name: "",
        score: "",
      }));
    }

    return (
      Object.entries(observationSpan?.evals_metrics || {}).map(
        ([id, item]) => ({
          id: id,
          name: item.name,
          score: item.score,
          explanation: item.explanation,
          loading: item?.loading,
          error: item?.error,
          outputType: item?.outputType,
          status: item?.status,
          skippedReason: item?.skipped_reason,
        }),
      ) || []
    );
  }, [observationSpan, isLoading]);

  // Function to handle opening the feedback modal
  const handleOpenFeedbackModal = (feedbackData) => {
    setSelectedAddFeedback(feedbackData);
    setIsModalOpen(true);
  };

  // Function to handle closing the feedback modal
  const handleCloseFeedbackModal = () => {
    setIsModalOpen(false);
  };

  const defaultColDef = {
    lockVisible: true,
    filter: false,
    resizable: true,
    suppressSizeToFit: false,
    minWidth: 150,
    cellStyle: {
      padding: 0,
      height: "100%",
      display: "flex",
      flex: 1,
      flexDirection: "column",
    },
    cellRenderer: EvalsCustomCellRenderer,
    headerComponent: EvalCustomHeaderCellRenderer,
    cellRendererParams: {
      setShowExplanationInColumn,
      showExplanationInColumn,
      showAddFeedback,
      showViewDetail,
      setSelectedAddFeedback: handleOpenFeedbackModal,
      selectedAddFeedback,
      setSelectedViewDetail,
    },
  };

  // if (isLoading) return <LinearProgress />;

  return (
    <Paper
      style={{
        width: "100%",
        height: "100%",
      }}
    >
      <Box
        sx={{
          height: "100%",
          width: "100%",
        }}
        className="ag-theme-quartz"
      >
        <AgGridReact
          ref={gridRef}
          columnDefs={cols}
          rowData={rows}
          domLayout="autoHeight"
          className={rows.length > 0 ? "no-min-height-grid" : undefined}
          headerHeight={44}
          rowHeight={62}
          suppressContextMenu
          suppressHorizontalScroll={false}
          suppressVerticalScroll={false}
          theme={agTheme}
          defaultColDef={defaultColDef}
          onGridReady={(params) => {
            gridRef.current = params;
            params.api.sizeColumnsToFit();
          }}
          getRowId={(params) => params.data.id}
          suppressCellSelection={true}
          rowStyle={{ cursor: "pointer" }}
        />
      </Box>
      <AddFeedbackForm
        open={isModalOpen}
        onClose={handleCloseFeedbackModal}
        selectedAddFeedback={selectedAddFeedback}
      />
      <ViewDetailsModal
        open={Boolean(selectedViewDetail)}
        onClose={() => setSelectedViewDetail(null)}
        title={selectedViewDetail?.name || "Function Calling"}
        selectedViewDetail={selectedViewDetail}
      />
      {/* Pass content to dialog */}
    </Paper>
  );
};

BottomEvalsTab.propTypes = {
  observationSpan: PropTypes.object,
  isLoading: PropTypes.bool,
  showViewDetail: PropTypes.bool,
  showAddFeedback: PropTypes.bool,
};

export default BottomEvalsTab;
