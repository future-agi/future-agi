import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, { useMemo } from "react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import axios, { endpoints } from "src/utils/axios";
import { useDatasetOptimizationResultContext } from "./context/DatasetOptimizationResultContext";
// Import shared cell renderers from simulation for consistent design
import TrialCellRenderer from "src/sections/test-detail/FixMyAgentDrawer/OptimizationResults/CellRenderers/TrialCellRenderer";
import AverageEvalCellRenderer from "src/sections/test-detail/FixMyAgentDrawer/OptimizationResults/CellRenderers/AverageEvalCellRenderer";
import OptimizeResultHeader from "src/sections/test-detail/FixMyAgentDrawer/OptimizationResults/CellRenderers/OptimizeResultHeader";
import PromptTooltip from "src/sections/test-detail/FixMyAgentDrawer/OptimizationResults/CellRenderers/PromptTooltip";

// Column configuration builder - matching simulation's pattern
const getColumnConfig = (columnConfig) => {
  if (!columnConfig || !Array.isArray(columnConfig)) return [];

  return columnConfig.map((column) => {
    switch (column?.id) {
      case "trial":
        return {
          field: "trial",
          headerName: "Trial",
          cellRenderer: TrialCellRenderer,
          minWidth: 200,
          isVisible: true,
          id: "trial",
          colId: "trial",
          valueGetter: (params) => {
            return {
              title: params.data?.trial,
              improvement: params.data?.score_percentage_change,
              isBest: params.data?.is_best,
            };
          },
        };

      case "prompt":
        return {
          field: "prompt",
          headerName: "Prompts",
          minWidth: 300,
          maxWidth: 400,
          flex: 1,
          id: "prompt",
          colId: "prompt",
          tooltipComponent: PromptTooltip,
          tooltipValueGetter: ({ data }) => data?.prompt,
          isVisible: true,
        };

      default:
        // Eval columns - values live under data.eval_scores keyed by column.id
        // each value is { score, percentage_change }.
        return {
          headerName: column?.name,
          minWidth: 170,
          colId: column?.id,
          cellRenderer: AverageEvalCellRenderer,
          headerComponent: OptimizeResultHeader,
          isVisible: true,
          id: column?.id,
          valueGetter: (params) => params.data?.eval_scores?.[column?.id],
        };
    }
  });
};

const defaultColDef = {
  lockVisible: true,
  sortable: false,
  filter: false,
  resizable: true,
  suppressMenu: true,
};

/**
 * Dataset Optimization Result Grid Component
 *
 * Similar to OptimizationResultGrid from simulation, but uses dataset optimization endpoints.
 */
const DatasetOptimizationResultGrid = ({ optimizationId, onTrialClick }) => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const { setGridApi } = useDatasetOptimizationResultContext();

  const { data: optimizationData } = useQuery({
    queryKey: ["dataset-optimization-details", optimizationId],
    queryFn: () =>
      axios.get(endpoints.develop.datasetOptimization.detail(optimizationId)),
    enabled: !!optimizationId,
    select: (data) => data?.data?.result,
  });

  const columnDefs = useMemo(() => {
    return getColumnConfig(optimizationData?.column_config);
  }, [optimizationData?.column_config]);

  const rowData = useMemo(() => {
    return optimizationData?.table ?? [];
  }, [optimizationData?.table]);

  return (
    <Box sx={{ height: "100%" }}>
      <AgGridReact
        theme={agTheme}
        rowSelection={undefined}
        autoSizeStrategy={{
          type: "fitCellContents",
        }}
        onGridReady={(params) => {
          setGridApi(params.api);
        }}
        className="dataset-optimization-result-grid"
        domLayout="autoHeight"
        columnDefs={columnDefs}
        defaultColDef={defaultColDef}
        pagination={false}
        paginationPageSizeSelector={false}
        rowData={rowData}
        rowStyle={{ cursor: "pointer" }}
        getRowId={({ data }) => data.id}
        tooltipShowDelay={200}
        tooltipInteraction={true}
        onCellClicked={(event) => {
          if (event?.data?.id && onTrialClick) {
            onTrialClick(event.data.id);
          }
        }}
      />
    </Box>
  );
};

DatasetOptimizationResultGrid.propTypes = {
  optimizationId: PropTypes.string.isRequired,
  onTrialClick: PropTypes.func,
};

export default DatasetOptimizationResultGrid;
