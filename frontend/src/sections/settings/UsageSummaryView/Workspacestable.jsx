import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import PropTypes from "prop-types";
import React, { useMemo, useCallback, useRef, useEffect } from "react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { EvaluationCellRender, TotalCellRenderer } from "./CustomCells";
import { fCurrency } from "src/utils/format-number";
import axiosInstance, { endpoints } from "../../../utils/axios";
import { getMonthAndYear, usageDefaultColDef } from "./common";

export default function WorkspaceUsageTable({
  currentTab,
  selectedMonth,
  setOpenBreakDownDrawer,
  setSelectedWorkspace,
  totalMetrics,
}) {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const gridRef = useRef();

  const getDataSource = useCallback(
    () => ({
      getRows: async (params) => {
        if (!selectedMonth) return;
        try {
          const endpoint = endpoints.settings.usageTotals;
          const res = await axiosInstance(endpoint, {
            params: getMonthAndYear(selectedMonth),
          });
          const response = res?.data;

          params.success({
            rowData: response?.result?.workspaces ?? [],
            rowCount: response?.result?.workspaces?.length ?? 0,
          });

          params.api.setGridOption("pinnedBottomRowData", [
            {
              name: "Total",
              overall: { ...totalMetrics?.credits_used },
              traces: { ...totalMetrics?.traces },
              evaluations: { ...totalMetrics?.evaluations },
              error_localizations: {
                ...totalMetrics?.error_localizations,
              },
              agent_compass: { ...totalMetrics?.agent_compass },
              simulate: { ...totalMetrics?.simulate },
            },
          ]);
        } catch (error) {
          params.fail();
        }
      },
    }),
    [selectedMonth, totalMetrics],
  );

  const columnDefs = useMemo(
    () => [
      {
        headerName: "Workspace",
        field: "name",
        flex: 1,
        cellRendererSelector: (params) =>
          params.node.rowPinned ? { component: TotalCellRenderer } : undefined,
      },
      {
        headerName: "Overall",
        field: "overall",
        flex: 1,
        valueGetter: (params) =>
          currentTab === "cost"
            ? params?.data?.overall?.cost
            : params?.data?.overall?.count,
        valueFormatter: (params) => {
          if (currentTab === "cost") {
            if (!params?.value) {
              return "$0";
            } else {
              return fCurrency(params.value, true);
            }
          } else {
            return undefined;
          }
        },
        cellRendererSelector: (params) =>
          params.node.rowPinned ? { component: TotalCellRenderer } : undefined,
      },
      {
        headerName: "Traces",
        field: "traces",
        flex: 1,
        valueGetter: (params) =>
          currentTab === "cost"
            ? params?.data?.traces?.cost
            : params?.data?.traces?.count,
        valueFormatter: (params) => {
          if (currentTab === "cost") {
            if (!params?.value) {
              return "$0";
            } else {
              return fCurrency(params.value, true);
            }
          } else {
            return undefined;
          }
        },
        cellRendererSelector: (params) =>
          params.node.rowPinned ? { component: TotalCellRenderer } : undefined,
      },
      {
        headerName: "Evaluations",
        field: "evaluations",
        flex: 1,
        valueGetter: (params) =>
          currentTab === "cost"
            ? params?.data?.evaluations?.cost
            : params?.data?.evaluations?.count,
        cellRenderer: EvaluationCellRender,
        cellRendererParams: {
          onViewBreakdown: (data) => {
            setOpenBreakDownDrawer(true);
            setSelectedWorkspace({
              id: data?.id,
              name: data?.name,
            });
          },
          currentTab,
        },
        valueFormatter: (params) => {
          if (currentTab === "cost") {
            if (!params?.value) {
              return "$0";
            } else {
              return fCurrency(params.value, true);
            }
          } else {
            return undefined;
          }
        },
        cellRendererSelector: (params) =>
          params.node.rowPinned ? { component: TotalCellRenderer } : undefined,
      },
      {
        headerName: "Error Localizations",
        field: "error_localizations",
        flex: 1,
        valueGetter: (params) =>
          currentTab === "cost"
            ? params?.data?.error_localizations?.cost
            : params?.data?.error_localizations?.count,
        valueFormatter: (params) => {
          if (currentTab === "cost") {
            if (!params?.value) {
              return "$0";
            } else {
              return fCurrency(params.value, true);
            }
          } else {
            return undefined;
          }
        },
        cellRendererSelector: (params) =>
          params.node.rowPinned ? { component: TotalCellRenderer } : undefined,
      },
      {
        headerName: "Error Feed",
        field: "agent_compass",
        flex: 1,
        valueGetter: (params) =>
          currentTab === "cost"
            ? params?.data?.agent_compass?.cost
            : params?.data?.agent_compass?.count,
        valueFormatter: (params) => {
          if (currentTab === "cost") {
            if (!params?.value) {
              return "$0";
            } else {
              return fCurrency(params.value, true);
            }
          } else {
            return undefined;
          }
        },
        cellRendererSelector: (params) =>
          params.node.rowPinned ? { component: TotalCellRenderer } : undefined,
      },
      {
        headerName: "Simulate",
        field: "simulate",
        flex: 1,
        valueGetter: (params) =>
          currentTab === "cost"
            ? params?.data?.simulate?.cost
            : params?.data?.simulate?.count,
        valueFormatter: (params) => {
          if (currentTab === "cost") {
            if (!params?.value) {
              return "$0";
            } else {
              return fCurrency(params.value, true);
            }
          } else {
            return undefined;
          }
        },
        cellRendererSelector: (params) =>
          params.node.rowPinned ? { component: TotalCellRenderer } : undefined,
      },
    ],
    [currentTab, setOpenBreakDownDrawer, setSelectedWorkspace],
  );

  const onGridReady = useCallback(
    (params) => {
      const dataSource = getDataSource();
      params.api.setGridOption("serverSideDatasource", dataSource);
    },
    [getDataSource],
  );

  useEffect(() => {
    if (!gridRef?.current?.api) return;
    const dataSource = getDataSource();
    gridRef.current?.api?.setGridOption("serverSideDatasource", dataSource);
  }, [getDataSource]);

  return (
    <Box sx={{ height: 500, width: "100%" }} className="ag-theme-quartz">
      <AgGridReact
        ref={gridRef}
        columnDefs={columnDefs}
        defaultColDef={usageDefaultColDef}
        rowModelType="serverSide"
        onGridReady={onGridReady}
        suppressServerSideFullWidthLoadingRow={true}
        theme={agTheme}
      />
    </Box>
  );
}

WorkspaceUsageTable.propTypes = {
  currentTab: PropTypes.string,
  selectedMonth: PropTypes.string,
  setOpenBreakDownDrawer: PropTypes.func,
  setSelectedWorkspace: PropTypes.func,
  totalMetrics: PropTypes.object,
};
