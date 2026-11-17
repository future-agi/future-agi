import { ROW_HEIGHT } from "src/theme/sizes";
import { Box, Stack, Typography } from "@mui/material";
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
import { formatDistanceToNow } from "date-fns";
import SvgColor from "src/components/svg-color";
import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useAlertStore } from "../../store/useAlertStore";
import { useAlertFilterShallow } from "../../store/useAlertFilterStore";
import {
  ALERT_LIST_MULTI_VALUE_FIELDS,
  buildFilterParams,
} from "../../store/alertFilterState";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { formatNumberWithCommas } from "../../../UsersView/common";
import _ from "lodash";
import {
  EARLIER_RULE_ROW_TEXT,
  getAlertFilterValue,
  isAlertMuted,
  normalizeAlertListRow,
  usesEarlierRule,
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
    setTotalRows,
    setCurrentPageAlertList,
    setColumns,
    setRefreshFn,
    columns: storeColumns,
  } = useAlertStore();

  const debouncedSearchTerm = useDebounce(searchQuery, 300);
  const { activeFilters } = useAlertFilterShallow();

  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  // The store is the single selection owner, including removals from the
  // confirmation dialog. No effect may restore IDs after a scope reset.
  const rowSelection = useMemo(
    () =>
      Object.fromEntries(
        selectedRows
          .filter((row) => row?.id != null)
          .map((row) => [row.id, true]),
      ),
    [selectedRows],
  );
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

  const extractedFilterObject = useMemo(
    () => buildFilterParams(activeFilters, ALERT_LIST_MULTI_VALUE_FIELDS),
    [activeFilters],
  );

  // Narrowing the result set can leave the current page out of range, and the
  // API does not clamp — it returns an empty table with a total that says
  // otherwise. Adjusted during render rather than in an effect: an effect runs
  // after commit, so the query would fire once for the stale page before the
  // reset landed. Keyed on the serialized filters because the panel re-emits
  // an equal-but-new object when it opens, which must not move the user.
  const resultSetKey = `${JSON.stringify(extractedFilterObject)}|${debouncedSearchTerm}`;
  const [prevResultSetKey, setPrevResultSetKey] = useState(resultSetKey);
  if (resultSetKey !== prevResultSetKey) {
    setPrevResultSetKey(resultSetKey);
    setPage(0);
  }

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
  // A row on its earlier rule carries a second line under its name.
  const rowHeight = items.some(usesEarlierRule)
    ? ROW_HEIGHT.twoLine
    : ROW_HEIGHT.default;

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
  const handleRowSelectionChange = useCallback((_selection, selected) => {
    useAlertStore.setState({
      selectedRows: selected,
      selectedAll: false,
      excludingIds: new Set(),
    });
  }, []);

  const columns = useMemo(
    () => [
      {
        id: "name",
        accessorKey: "name",
        header: "Issues",
        meta: { flex: 2 },
        minSize: 120,
        cell: ({ getValue, row }) => {
          const isMuted = isAlertMuted(row.original);
          const label = getValue();
          // The grid cell is a flex row that clips. Without minWidth 0 this
          // box kept the whole name's width, so the cell edge cut the name
          // mid-word and hid the mute icon. Now the box fits the cell, the
          // name block ends in "…", the full name stays in its title and the
          // mute icon stays beside it.
          const name = (
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                minWidth: 0,
              }}
            >
              <Typography
                variant="body2"
                noWrap
                title={typeof label === "string" ? label : undefined}
                sx={{ fontWeight: 500, minWidth: 0 }}
                data-alert-row-name={row.original?.name}
                data-alert-row-id={row.original?.id}
              >
                {label}
              </Typography>
              {isMuted && (
                <SvgColor
                  src="/assets/icons/ic_mute.svg"
                  sx={{
                    height: 14,
                    width: 14,
                    flexShrink: 0,
                    bgcolor: "status.danger.solid",
                  }}
                />
              )}
            </Box>
          );
          if (!usesEarlierRule(row.original)) return name;
          return (
            <Stack
              sx={{ height: "100%", minWidth: 0, justifyContent: "center" }}
            >
              {name}
              <Typography
                variant="s3"
                color="text.secondary"
                noWrap
                title={EARLIER_RULE_ROW_TEXT}
                data-alert-row-rule="earlier"
              >
                {EARLIER_RULE_ROW_TEXT}
              </Typography>
            </Stack>
          );
        },
      },
      {
        id: "status",
        accessorKey: "status",
        header: "Status",
        size: 100,
        enableSorting: false,
        cell: ({ getValue }) => <StatusCell value={getValue()} />,
      },
      {
        id: "metricType",
        accessorKey: "metricType",
        header: "Alert Type",
        meta: { flex: 1 },
        minSize: 130,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography variant="body2" noWrap sx={{ fontSize: 12 }}>
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
              <Typography variant="body2" sx={{ fontSize: 12 }}>
                -
              </Typography>
            );
          try {
            return (
              <Typography variant="body2" noWrap sx={{ fontSize: 12 }}>
                {formatDistanceToNow(new Date(val), { addSuffix: true })}
              </Typography>
            );
          } catch {
            return (
              <Typography variant="body2" sx={{ fontSize: 12 }}>
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
        size: 100,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Typography variant="body2" sx={{ fontSize: 12 }}>
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
              <Typography variant="body2" noWrap sx={{ fontSize: 12 }}>
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

        <DataTable
          columns={columns}
          data={items}
          isLoading={isLoading}
          rowCount={total}
          rowSelection={rowSelection}
          selectionScope={[
            page,
            pageSize,
            searchQuery,
            debouncedSearchTerm,
            extractedFilterObject,
            observeId,
            mainPage,
          ]}
          onRowSelectionChange={handleRowSelectionChange}
          columnVisibility={columnVisibility}
          onRowClick={handleRowClick}
          getRowId={(row) => row.id}
          enableSelection
          rowHeight={rowHeight}
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
