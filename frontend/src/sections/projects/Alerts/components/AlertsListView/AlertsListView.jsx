import { Box, Typography } from "@mui/material";
import React, {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import Actions from "./Actions";
import { StatusCell, TrendChartCell } from "./AlertCells";
import FilterChipsRenderer from "../../../../common/EvalsTasks/Renderers/FilterChipsRenderer";
import AlertFilters from "../AlertFilters";
import { formatDistanceToNow } from "date-fns";
import SvgColor from "src/components/svg-color";
import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useAlertStore } from "../../store/useAlertStore";
import { useAlertFilterShallow } from "../../store/useAlertFilterStore";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { formatNumberWithCommas } from "../../../UsersView/common";
import _ from "lodash";
import {
  getAlertFilterValue,
  isAlertMuted,
  normalizeAlertListRow,
} from "../../common";

const AlertsSheetView = lazy(
  () => import("../AlertsSheetView/AlertsSheetView"),
);

export default function AlertsListView() {
  const queryClient = useQueryClient();
  const {
    handleOpenSheetView,
    selectedProject: observeId,
    mainPage,
    searchQuery,
    setHasData,
    selectedRows,
    setSelectedRows,
    setTotalRows,
    setCurrentPageAlertList,
    setColumns,
    setRefreshFn,
    columns: storeColumns,
  } = useAlertStore();

  const debouncedSearchTerm = useDebounce(searchQuery, 300);
  const {
    activeFilters,
    hasValidFilters,
    showFilterSection: showFilter,
  } = useAlertFilterShallow();

  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [rowSelection, setRowSelection] = useState({});
  // Derive column visibility from the store's columns (set by ColumnDropdown via Actions)
  const columnVisibility = useMemo(() => {
    if (!storeColumns || storeColumns.length === 0) {
      // Default: hide trends and filters
      return { trends: false, filters: false };
    }
    const vis = {};
    for (const col of storeColumns) {
      const key = _.camelCase(col.id);
      if (col.isVisible === false) {
        vis[key] = false;
      }
    }
    // Always apply defaults for initially-hidden columns
    if (!Object.prototype.hasOwnProperty.call(vis, "trends"))
      vis.trends = false;
    if (!Object.prototype.hasOwnProperty.call(vis, "filters"))
      vis.filters = false;
    return vis;
  }, [storeColumns]);

  const extractedFilterObject = useMemo(() => {
    if (!hasValidFilters) return null;
    const filterObj = activeFilters.reduce((acc, filter) => {
      const { filterType, filterValue } = filter;
      if (Array.isArray(filterValue) && filterValue.length > 0) {
        acc[filterType] = filterValue;
      } else if (typeof filterValue === "string" && filterValue.trim() !== "") {
        acc[filterType] = filterValue;
      }
      return acc;
    }, {});
    return Object.keys(filterObj).length > 0 ? filterObj : null;
  }, [activeFilters, hasValidFilters]);

  // Register refresh function in the store
  const refreshFn = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["alerts-list"] });
  }, [queryClient]);

  useEffect(() => {
    setRefreshFn(refreshFn);
    return () => setRefreshFn(null);
  }, [refreshFn, setRefreshFn]);

  // Fetch data
  const { data, isLoading } = useQuery({
    queryKey: [
      "alerts-list",
      page,
      pageSize,
      debouncedSearchTerm,
      extractedFilterObject,
      mainPage ? null : observeId,
      mainPage,
    ],
    queryFn: () => {
      const rawParams = {
        page_number: page,
        page_size: pageSize,
        search_text: debouncedSearchTerm || undefined,
        ...(!mainPage && observeId ? { project_id: observeId } : {}),
        ...extractedFilterObject,
      };
      return axios.get(endpoints.project.getMonitorList(), {
        params: rawParams,
        paramsSerializer: (params) => {
          const searchParams = new URLSearchParams();
          Object.entries(params).forEach(([key, value]) => {
            if (value === undefined || value === null || value === "") return;
            if (key === "project_id" && Array.isArray(value)) {
              value.forEach((v) => v && searchParams.append("project_id", v));
            } else {
              searchParams.append(key, value);
            }
          });
          return searchParams.toString();
        },
      });
    },
    select: (d) => d.data?.result,
    keepPreviousData: true,
    refetchInterval: 10000,
  });

  const items = useMemo(
    () => (data?.table ?? []).map(normalizeAlertListRow),
    [data],
  );
  const total = data?.metadata?.total_rows ?? 0;

  // Sync store state on data change
  useEffect(() => {
    if (data) {
      setTotalRows(total);
      setCurrentPageAlertList(items);
      setHasData(
        items.length > 0 || !!debouncedSearchTerm || !!extractedFilterObject,
      );
      // Set column config from server
      const excludedKeys = ["created_at", "updated_at"];
      const filteredColumns = data?.column_config?.filter(
        (col) => !excludedKeys.includes(col.id),
      );
      if (filteredColumns) {
        setColumns(filteredColumns);
      }
    }
  }, [
    data,
    items,
    total,
    debouncedSearchTerm,
    extractedFilterObject,
    setTotalRows,
    setCurrentPageAlertList,
    setHasData,
    setColumns,
  ]);

  // Sync row selection → store selectedRows
  const handleRowSelectionChange = useCallback(
    (newSelection) => {
      setRowSelection(newSelection);
      const newSelected = Object.keys(newSelection)
        .filter((k) => newSelection[k])
        .map((k) => items[parseInt(k, 10)])
        .filter(Boolean);
      setSelectedRows(newSelected);
    },
    [items, setSelectedRows],
  );

  // When store's handleCancelSelection is called, also clear local rowSelection
  useEffect(() => {
    if (selectedRows.length === 0) {
      setRowSelection({});
    }
  }, [selectedRows]);

  const columns = useMemo(
    () => [
      {
        id: "name",
        accessorKey: "name",
        header: "Issues",
        meta: { flex: 2 },
        minSize: 200,
        cell: ({ getValue, row }) => {
          const isMuted = isAlertMuted(row.original);
          return (
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
              <Typography
                variant="body2"
                noWrap
                sx={{ fontWeight: 500 }}
                data-alert-row-name={row.original?.name}
                data-alert-row-id={row.original?.id}
              >
                {getValue()}
              </Typography>
              {isMuted && (
                <SvgColor
                  src="/assets/icons/ic_mute.svg"
                  sx={{
                    height: 14,
                    width: 14,
                    flexShrink: 0,
                    bgcolor: "red.500",
                  }}
                />
              )}
            </Box>
          );
        },
      },
      {
        id: "status",
        accessorKey: "status",
        header: "Status",
        size: 110,
        enableSorting: false,
        cell: ({ getValue }) => <StatusCell value={getValue()} />,
      },
      {
        id: "metricType",
        accessorKey: "metricType",
        header: "Alert Type",
        meta: { flex: 1 },
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography variant="body2" sx={{ fontSize: 13 }}>
            {getValue() || "-"}
          </Typography>
        ),
      },
      {
        id: "lastTriggered",
        accessorKey: "lastTriggered",
        header: "Last Triggered",
        size: 140,
        enableSorting: false,
        cell: ({ getValue }) => {
          const val = getValue();
          if (!val || val === "-")
            return (
              <Typography variant="body2" sx={{ fontSize: 13 }}>
                -
              </Typography>
            );
          try {
            return (
              <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>
                {formatDistanceToNow(new Date(val), { addSuffix: true })}
              </Typography>
            );
          } catch {
            return (
              <Typography variant="body2" sx={{ fontSize: 13 }}>
                -
              </Typography>
            );
          }
        },
      },
      {
        id: "noOfAlerts",
        accessorKey: "noOfAlerts",
        header: "No. of triggers",
        size: 120,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography variant="body2" sx={{ fontSize: 13 }}>
            {formatNumberWithCommas(getValue())}
          </Typography>
        ),
      },
      {
        id: "updatedAt",
        accessorKey: "updated_at",
        header: "Updated at",
        size: 140,
        enableSorting: false,
        cell: ({ getValue, row }) => {
          const val = getValue() || row.original.created_at;
          if (!val) return null;
          try {
            return (
              <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>
                {formatDistanceToNow(new Date(val), { addSuffix: true })}
              </Typography>
            );
          } catch {
            return null;
          }
        },
      },
      {
        id: "trends",
        accessorKey: "trends",
        header: "Trend",
        meta: { flex: 1 },
        enableSorting: false,
        cell: ({ row }) => {
          const trends = row.original.trends;
          const value = trends?.map((t) => ({
            timestamp: t?.timestamp,
            value: t?.count,
          }));
          return <TrendChartCell value={value} />;
        },
      },
      {
        id: "filters",
        accessorKey: "filters",
        header: "Filters",
        meta: { flex: 1 },
        enableSorting: false,
        cell: ({ row }) => {
          const filterValues = getAlertFilterValue(row.original);
          return <FilterChipsRenderer value={filterValues} />;
        },
      },
    ],
    [],
  );

  const handleRowClick = useCallback(
    (row) => {
      if (row?.id) {
        handleOpenSheetView(row.id);
        trackEvent(Events.alertClicked, {
          [PropertyName.id]: row.id,
        });
      }
    },
    [handleOpenSheetView],
  );

  return (
    <>
      <Box
        sx={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          gap: 1.5,
          overflow: "hidden",
          minHeight: 0,
        }}
      >
        <Actions />
        {showFilter && <AlertFilters />}

        <DataTable
          columns={columns}
          data={items}
          isLoading={isLoading}
          rowCount={total}
          rowSelection={rowSelection}
          onRowSelectionChange={handleRowSelectionChange}
          columnVisibility={columnVisibility}
          onRowClick={handleRowClick}
          getRowId={(row) => row.id}
          enableSelection
          rowHeight={44}
          emptyMessage="No alerts found"
        />

        <DataTablePagination
          page={page}
          pageSize={pageSize}
          total={total}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(0);
          }}
        />
      </Box>
      <Suspense>
        <AlertsSheetView />
      </Suspense>
    </>
  );
}
