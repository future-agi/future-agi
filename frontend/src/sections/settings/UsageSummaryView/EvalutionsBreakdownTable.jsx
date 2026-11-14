import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import "src/utils/ag-grid-setup";
import PropTypes from "prop-types";
import React, { useMemo, useCallback, useRef, useEffect } from "react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { TotalCellRenderer } from "./CustomCells";
import { fCurrency } from "src/utils/format-number";
import axiosInstance, { endpoints } from "../../../utils/axios";
import { getMonthAndYear, usageDefaultColDef } from "./common";
import { useQuery } from "@tanstack/react-query";
import _ from "lodash";

const USAGE_TABLE_THEME_PARAMS = {
  headerColumnBorder: { width: "0px", rowVerticalPaddingScale: 2.6 },
};

export default function NoWorkspaceUsageTable({
  currentTab,
  selectedMonth,
  workspaceId: workspaceIdProp,
}) {
  const agTheme = useAgThemeWith(USAGE_TABLE_THEME_PARAMS);
  const gridRef = useRef();

  // Only auto-detect workspace when no explicit workspaceId is provided
  const { data: workSpacesData } = useQuery({
    queryKey: ["workspaces", selectedMonth],
    queryFn: async () => {
      return axiosInstance.get(endpoints.settings.usageTotals, {
        params: getMonthAndYear(selectedMonth),
      });
    },
    enabled: !!selectedMonth && !workspaceIdProp,
    select: (d) => d?.data?.result,
  });
  const workspaceId = workspaceIdProp || workSpacesData?.workspaces?.[0]?.id;

  const getDataSource = useCallback(
    () => ({
      getRows: async (params) => {
        try {
          if (!selectedMonth || !workspaceId) return;
          const endpoint = `${endpoints.settings.workspaceUsage}`;
          const res = await axiosInstance(endpoint, {
            params: {
              ...getMonthAndYear(selectedMonth),
              workspace_id: workspaceId,
            },
          });
          const response = res?.data;

          params.success({
            rowData: response?.result?.evaluations ?? [],
            rowCount: response?.result?.evaluations?.length ?? 0,
          });

          params.api.setGridOption("pinnedBottomRowData", [
            {
              name: "Total",
              cost: response?.result?.total?.cost,
              count: response?.result?.total?.count,
            },
          ]);
        } catch (error) {
          params.fail();
        }
      },
    }),
    [selectedMonth, workspaceId],
  );

  const columnDefs = useMemo(
    () => [
      {
        headerName: "Evaluations",
        field: "name",
        flex: 3,
        valueFormatter: (params) => {
          if (!params.node.rowPinned && typeof params.value === "string") {
            return _.startCase(params.value.replace(/_/g, " "));
          }
          return params.value;
        },
        cellRendererSelector: (params) =>
          params.node.rowPinned ? { component: TotalCellRenderer } : undefined,
      },
      {
        headerName: currentTab === "cost" ? "Cost" : "Count",
        field: currentTab === "cost" ? "cost" : "count",
        flex: 1,
        cellRendererSelector: (params) =>
          params.node.rowPinned ? { component: TotalCellRenderer } : undefined,
        cellRendererParams: {
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
      },
    ],
    [currentTab],
  );

  const onGridReady = useCallback(
    (params) => {
      params.api.setGridOption("serverSideDatasource", getDataSource());
    },
    [getDataSource],
  );

  useEffect(() => {
    if (!gridRef?.current?.api) return;
    const dataSource = getDataSource();
    gridRef?.current?.api?.setGridOption("serverSideDatasource", dataSource);
  }, [getDataSource]);

  return (
    <Box sx={{ height: 500, width: "100%" }} className="ag-theme-quartz">
      <AgGridReact
        ref={gridRef}
        columnDefs={columnDefs}
        defaultColDef={usageDefaultColDef}
        rowModelType="serverSide"
        onGridReady={onGridReady}
        getRowId={({ data }) => data.name}
        suppressServerSideFullWidthLoadingRow={true}
        theme={agTheme}
      />
    </Box>
  );
}

NoWorkspaceUsageTable.propTypes = {
  currentTab: PropTypes.string,
  selectedMonth: PropTypes.string,
  workspaceId: PropTypes.string,
};
