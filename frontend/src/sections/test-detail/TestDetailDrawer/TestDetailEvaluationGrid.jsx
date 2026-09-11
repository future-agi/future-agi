import { Box, Skeleton } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, { useMemo } from "react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import PropTypes from "prop-types";
import EvalViewButtonRenderer from "../CellRenderers/EvalViewButtonRenderer";
import StatusCellRenderer from "src/sections/develop-detail/DataTab/DatapointDrawerV2/StatusCellRenderer";
import EvalSectionDetailDrawer from "./EvalSectionDetailDrawer";
import { TestRunLoadingStatus } from "../common";

const DEFAULT_COL_DEF = {
  flex: 1,
  resizable: true,
  sortable: false,
  filter: false,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
};

const TestDetailEvaluationGrid = ({ evalOutputs, callStatus }) => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const columnDefs = useMemo(
    () => [
      {
        headerName: "Evaluation metrics",
        field: "metric",
        cellRenderer: (params) => {
          if (params.data?.isLoading) {
            return (
              <Box
                sx={{
                  paddingX: 1,
                  display: "flex",
                  alignItems: "center",
                  height: "100%",
                }}
              >
                <Skeleton
                  sx={{ width: "100%", height: "10px" }}
                  variant="rounded"
                />
              </Box>
            );
          }
          return params.value;
        },
      },
      {
        headerName: "Score",
        field: "score",
        cellRenderer: (params) => {
          return (
            <StatusCellRenderer
              cellValue={params.data?.metricDetails?.value}
              status={params.data?.metricDetails?.error ? "error" : "success"}
              isLoading={params.data?.isLoading}
              type={params.data?.metricDetails?.type}
            />
          );
        },
      },
      {
        headerName: "Description",
        field: "view",
        cellRenderer: EvalViewButtonRenderer,
      },
    ],
    [],
  );

  const rowData = useMemo(() => {
    return Object.entries(evalOutputs || {})?.map(([id, metric]) => {
      const isLoading =
        Object.keys(metric || {}).length === 0 &&
        TestRunLoadingStatus.includes(callStatus?.toLowerCase());

      const defaultVals = {
        metric: metric.name,
        id,
        metricDetails: { ...metric, id },
        isLoading,
      };

      return defaultVals;
    });
  }, [evalOutputs, callStatus]);

  return (
    <Box
      sx={{
        borderColor: "divider",
        display: "flex",
        flexDirection: "column",
        gap: 1,
      }}
    >
      <Box sx={{ height: 400 }}>
        <AgGridReact
          columnDefs={columnDefs}
          defaultColDef={DEFAULT_COL_DEF}
          rowData={rowData}
          theme={agTheme}
        />
      </Box>
      <EvalSectionDetailDrawer />
    </Box>
  );
};

TestDetailEvaluationGrid.propTypes = {
  evalOutputs: PropTypes.object,
  callStatus: PropTypes.string,
};

export default TestDetailEvaluationGrid;
