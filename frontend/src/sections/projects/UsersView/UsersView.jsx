import React, {
  lazy,
  Suspense,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { Box, CircularProgress, Typography } from "@mui/material";
import { useParams, useLocation, useNavigate } from "react-router";
import { Helmet } from "react-helmet-async";
import { formatDate } from "src/utils/report-utils";
import { endOfToday, sub } from "date-fns";
import { useUrlState } from "src/routes/hooks/use-url-state";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import { useObserveHeader } from "src/sections/project/context/ObserveHeaderContext";
import {
  useUpdateSavedView,
  useUpdateWorkspaceSavedView,
} from "src/api/project/saved-views";
import { enqueueSnackbar } from "notistack";

const USERS_TAB_TYPE = "users";

// Shared observe components
import ObserveToolbar from "../LLMTracing/ObserveToolbar";
import FilterChips from "../LLMTracing/FilterChips";
import CustomColumnDialog from "../LLMTracing/CustomColumnDialog";
import { useLLMTracingFilters } from "../LLMTracing/useLLMTracingFilters";
import ColumnConfigureDropDown from "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown";

// Lazy-load graph
const PrimaryGraph = lazy(
  () => import("../LLMTracing/GraphSection/PrimaryGraph"),
);

// User-specific
import useUsersStore from "./Store/usersStore";
import { getUsersColumnConfig } from "./common";
import UsersGrid from "./UsersGrid";
import UsersEmptyScreen from "./UsersEmptyScreen";
import { useShallow } from "zustand/react/shallow";
import { filtersContentEqual } from "../saved-view-utils";

// ---------------------------------------------------------------------------
// User filter fields for TraceFilterPanel
// ---------------------------------------------------------------------------
const USER_FILTER_FIELDS = [
  { id: "user_id", name: "User ID", category: "system", type: "string" },
  {
    id: "num_traces",
    name: "No. of Traces",
    category: "system",
    type: "number",
  },
  {
    id: "num_sessions",
    name: "No. of Sessions",
    category: "system",
    type: "number",
  },
  {
    id: "total_cost",
    name: "Total Cost ($)",
    category: "system",
    type: "number",
  },
  {
    id: "total_tokens",
    name: "Total Tokens",
    category: "system",
    type: "number",
  },
  {
    id: "avg_trace_latency",
    name: "Avg Latency / Trace (ms)",
    category: "system",
    type: "number",
  },
  {
    id: "num_llm_calls",
    name: "No. of LLM Calls",
    category: "system",
    type: "number",
  },
  {
    id: "eval_score",
    name: "Evals Pass Rate (%)",
    category: "system",
    type: "number",
  },
];

// Default filter and date range
const defaultFilterBase = [
  {
    columnId: "",
    filterConfig: { filterType: "", filterOp: "", filterValue: "" },
  },
];

const getDefaultDateRange = () => ({
  dateFilter: [
    formatDate(sub(new Date(), { days: 90 })),
    formatDate(endOfToday()),
  ],
  dateOption: "3M",
});

const getDateLabel = (dateFilter) => {
  if (!dateFilter) return "Past 3M";
  return dateFilter.dateOption === "Custom"
    ? "Custom range"
    : dateFilter.dateOption || "Past 3M";
};

const noopExtraProperties = () => ({});

const UsersView = ({
  savedViewApiRef = null,
  // Optional override for activeViewConfig — used by callers (e.g. UserList)
  // that don't wrap UsersView in ObserveHeaderProvider but still want
  // canSaveView to reflect divergence from a saved view's baseline.
  activeViewConfig: activeViewConfigProp,
}) => {
  const { observeId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const isObservePath = location.pathname.includes("observe");

  const {
    clearSelection,
    resetStore,
    gridApi,
    columns,
    setColumns,
    updateColumnVisibility,
    addCustomColumns,
    removeCustomColumns,
    openCustomColumnDialog,
    setOpenCustomColumnDialog,
  } = useUsersStore(
    useShallow((state) => ({
      clearSelection: state.clearSelection,
      resetStore: state.resetStore,
      gridApi: state.gridApi,
      columns: state.columns,
      setColumns: state.setColumns,
      updateColumnVisibility: state.updateColumnVisibility,
      addCustomColumns: state.addCustomColumns,
      removeCustomColumns: state.removeCustomColumns,
      openCustomColumnDialog: state.openCustomColumnDialog,
      setOpenCustomColumnDialog: state.setOpenCustomColumnDialog,
    })),
  );

  // --- Column visibility popover anchor ---
  const [columnConfigureAnchor, setColumnConfigureAnchor] = useState(null);
  const openColumnConfigure = Boolean(columnConfigureAnchor);

  // --- Auto-size columns (mirrors SessionsView / LLMTracingView) ---
  const [autoSizeAllCols, setAutoSizeAllCols] = useState(false);
  const handleAutoSize = useCallback(() => {
    if (!gridApi) return;
    const allColumnIds = [];
    gridApi.getColumnDefs()?.forEach((column) => {
      if (column?.field) allColumnIds.push(column.field);
    });
    if (!autoSizeAllCols) {
      setAutoSizeAllCols(true);
      gridApi.autoSizeColumns(allColumnIds, false);
    } else {
      setAutoSizeAllCols(false);
      gridApi.sizeColumnsToFit();
    }
  }, [gridApi, autoSizeAllCols]);

  // --- Eval attributes for custom column dialog (mirrors LLMTracingView) ---
  const { data: evalAttributes } = useQuery({
    queryKey: ["eval-attributes", observeId],
    queryFn: () =>
      axios.get(endpoints.project.getEvalAttributeList(), {
        params: {
          filters: JSON.stringify({ project_id: observeId }),
        },
      }),
    select: (data) => data.data?.result,
    enabled: Boolean(observeId),
  });
  const attributes = useMemo(() => evalAttributes || [], [evalAttributes]);

  // --- Observe header refresh wiring (TH-4023) ---
  // Expose a refresh callback to the shared ObserveHeader so the refresh
  // button in the header triggers an ag-grid serverSide refresh on this
  // Users tab.
  const {
    setHeaderConfig,
    activeViewConfig: activeViewConfigCtx,
    setActiveViewConfig,
    registerGetViewConfig,
    registerGetTabType,
  } = useObserveHeader();
  // Prefer prop (set by UserList for /dashboard/users) over context
  // (set by ObservePage for the Users fixed tab).
  const activeViewConfig = activeViewConfigProp ?? activeViewConfigCtx;

  const refreshUsers = useCallback(() => {
    if (gridApi) {
      gridApi.refreshServerSide();
    }
  }, [gridApi]);

  useEffect(() => {
    setHeaderConfig((prev) => ({
      ...prev,
      text: "Users",
      refreshData: refreshUsers,
    }));
  }, [refreshUsers, setHeaderConfig]);

  // --- Filter & date state ---
  const defaultDateFilter = useMemo(() => getDefaultDateRange(), []);

  const { filters, setFilters, validatedFilters, setDateFilter, dateFilter } =
    useLLMTracingFilters(
      defaultFilterBase,
      defaultDateFilter,
      "userFilter",
      "userDateFilter",
      [],
      noopExtraProperties,
    );

  // --- Extra filters from TraceFilterPanel (popover) ---
  const [extraFilters, setExtraFilters] = useState([]);
  const [isFilterOpen, setIsFilterOpen] = useUrlState("userFilterOpen", false);

  const hasActiveFilter = extraFilters.length > 0;

  // --- Display panel state ---
  const [showErrors, setShowErrors] = useUrlState("userShowErrors", false);
  const [showNonAnnotated, setShowNonAnnotated] = useUrlState(
    "userShowNonAnnotated",
    false,
  );
  const [hasEvalFilter, setHasEvalFilter] = useUrlState(
    "userHasEvalFilter",
    false,
  );
  const [showCompare, setShowCompare] = useUrlState("userShowCompare", false);

  // Combine validated filters with extra filters
  // extraFilters from ObserveToolbar use snake_case keys (column_id, filter_config)
  // validatedFilters from useLLMTracingFilters use camelCase keys (columnId, filterConfig)
  // Normalize extra filters to camelCase so useGetValidatedFilters in UsersGrid accepts them
  const finalFilters = useMemo(() => {
    if (!extraFilters.length) return validatedFilters;

    // ObserveToolbar number operators → Zod AllowedOperators
    const opFixMap = {
      equal_to: "equals",
      not_equal_to: "not_equals",
      not_between: "not_in_between",
    };

    const normalized = extraFilters.map((f) => {
      const rawOp =
        f.filter_config?.filter_op || f.filterConfig?.filterOp || "equals";
      const rawType =
        f.filter_config?.filter_type || f.filterConfig?.filterType || "text";
      const rawValue =
        f.filter_config?.filter_value ?? f.filterConfig?.filterValue ?? "";

      // Number values arrive as comma-joined strings; Zod expects arrays
      let filterValue = rawValue;
      if (rawType === "number" && typeof rawValue === "string") {
        filterValue = rawValue.includes(",") ? rawValue.split(",") : [rawValue];
      }

      return {
        columnId: f.column_id || f.columnId || "",
        _meta: { parentProperty: "" },
        filterConfig: {
          filterType: rawType,
          filterOp: opFixMap[rawOp] || rawOp,
          filterValue,
          ...(f.filter_config?.col_type && {
            col_type: f.filter_config.col_type,
          }),
        },
      };
    });
    return [...validatedFilters, ...normalized];
  }, [validatedFilters, extraFilters]);

  // --- Row height ---
  const [cellHeight, setCellHeight] = useUrlState("userCellHeight", "Short");

  // --- Grid state ---
  const [hasData, setHasData] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [searchState, setSearchState] = useState("loading");

  // Pass finalFilters into the users store so UsersGrid can use them
  useEffect(() => {
    useUsersStore.setState({ filters: finalFilters });
  }, [finalFilters]);

  // ---------------------------------------------------------------------------
  // Saved-view api — populates a ref that the parent UsersPageTabBar drives.
  // Captures display + filter state into config, and restores it on activation.
  // ---------------------------------------------------------------------------
  const getConfig = useCallback(() => {
    const visibleColumns = (columns || []).reduce((acc, col) => {
      acc[col.id] = col.isVisible !== false;
      return acc;
    }, {});
    // Capture full grid column state (widths, order, sort) so saved views
    // restore the user's exact column layout. Stash inside `display` since
    // the backend serializer's allowed_keys whitelists `display` for
    // arbitrary subkeys but does not whitelist a separate `columnState`.
    const columnState = gridApi?.getColumnState?.() ?? undefined;
    return {
      display: {
        cellHeight,
        showErrors,
        showNonAnnotated,
        hasEvalFilter,
        visibleColumns,
        ...(columnState ? { columnState } : {}),
      },
      filters: {
        extraFilters,
        dateFilter,
      },
    };
  }, [
    columns,
    cellHeight,
    showErrors,
    showNonAnnotated,
    hasEvalFilter,
    extraFilters,
    dateFilter,
    gridApi,
  ]);

  // Pending column state from a saved view that arrived before the grid was
  // ready. Drained by the effect below when gridApi becomes available.
  const pendingColumnStateRef = useRef(null);

  const applyConfig = useCallback(
    (config) => {
      if (!config) {
        // Reset to defaults
        setExtraFilters([]);
        setCellHeight("Short");
        setShowErrors(false);
        setShowNonAnnotated(false);
        setHasEvalFilter(false);
        setDateFilter(getDefaultDateRange());
        pendingColumnStateRef.current = null;
        // Reset column visibility to the column-config defaults so going back
        // to All Users from a saved view with limited columns shows everything
        // again.
        const defaultsVisibility = (getUsersColumnConfig() || []).reduce(
          (acc, col) => {
            acc[col.field] = col.hide === undefined ? true : !col.hide;
            return acc;
          },
          {},
        );
        if (Object.keys(defaultsVisibility).length > 0) {
          updateColumnVisibility(defaultsVisibility);
        }
        // Reset AG Grid column state (widths/order/sort) to coldef defaults.
        if (gridApi?.resetColumnState) gridApi.resetColumnState();
        return;
      }
      const display = config.display || {};
      const filtersCfg = config.filters || {};
      if (display.cellHeight) setCellHeight(display.cellHeight);
      if (typeof display.showErrors === "boolean")
        setShowErrors(display.showErrors);
      if (typeof display.showNonAnnotated === "boolean")
        setShowNonAnnotated(display.showNonAnnotated);
      if (typeof display.hasEvalFilter === "boolean")
        setHasEvalFilter(display.hasEvalFilter);
      if (display.visibleColumns && columns?.length) {
        updateColumnVisibility(display.visibleColumns);
      }
      if (Array.isArray(display.columnState) && display.columnState.length > 0) {
        if (gridApi?.applyColumnState) {
          gridApi.applyColumnState({
            state: display.columnState,
            applyOrder: true,
          });
        } else {
          pendingColumnStateRef.current = display.columnState;
        }
      }
      if (Array.isArray(filtersCfg.extraFilters)) {
        setExtraFilters(filtersCfg.extraFilters);
      }
      if (filtersCfg.dateFilter) {
        setDateFilter(filtersCfg.dateFilter);
      }
    },
    [
      setCellHeight,
      setShowErrors,
      setShowNonAnnotated,
      setHasEvalFilter,
      setDateFilter,
      setExtraFilters,
      updateColumnVisibility,
      columns,
      gridApi,
    ],
  );

  // Drain any column state queued before the grid was ready.
  useEffect(() => {
    if (gridApi?.applyColumnState && pendingColumnStateRef.current) {
      gridApi.applyColumnState({
        state: pendingColumnStateRef.current,
        applyOrder: true,
      });
      pendingColumnStateRef.current = null;
    }
  }, [gridApi]);

  // Keep the ref's handles in sync with the latest closures
  useEffect(() => {
    if (savedViewApiRef) {
      savedViewApiRef.current = { getConfig, applyConfig };
    }
  }, [savedViewApiRef, getConfig, applyConfig]);

  // "Save view" surfaces only on a custom saved-view tab when the live state
  // diverges from its saved baseline. UsersView's config nests dateFilter
  // inside `filters` (not `display` like LLMTracingView/SessionsView).
  const canSaveView = useMemo(() => {
    if (!activeViewConfig) return false;

    const baselineFilters = activeViewConfig.filters || {};
    const baselineDisplay = activeViewConfig.display || {};
    const baselineExtraFilters = baselineFilters.extraFilters || [];
    const baselineDateOption =
      baselineFilters.dateFilter?.dateOption ?? null;

    if (!filtersContentEqual(extraFilters, baselineExtraFilters)) return true;
    if ((dateFilter?.dateOption ?? null) !== baselineDateOption) return true;
    if (
      baselineDisplay.cellHeight !== undefined &&
      baselineDisplay.cellHeight !== cellHeight
    ) {
      return true;
    }
    if (
      baselineDisplay.showErrors !== undefined &&
      baselineDisplay.showErrors !== showErrors
    ) {
      return true;
    }
    if (
      baselineDisplay.showNonAnnotated !== undefined &&
      baselineDisplay.showNonAnnotated !== showNonAnnotated
    ) {
      return true;
    }
    if (
      baselineDisplay.hasEvalFilter !== undefined &&
      baselineDisplay.hasEvalFilter !== hasEvalFilter
    ) {
      return true;
    }
    // Column visibility: compare baseline visibleColumns dict against current
    // columns Zustand state. Only check columns the baseline knows about —
    // newly-added columns from a backend schema bump shouldn't mark dirty.
    if (
      baselineDisplay.visibleColumns &&
      typeof baselineDisplay.visibleColumns === "object"
    ) {
      const currentVisibility = (columns || []).reduce((acc, col) => {
        acc[col.id] = col.isVisible !== false;
        return acc;
      }, {});
      for (const colId of Object.keys(baselineDisplay.visibleColumns)) {
        const baselineVisible = baselineDisplay.visibleColumns[colId];
        const currentVisible = currentVisibility[colId];
        if (
          currentVisible !== undefined &&
          currentVisible !== baselineVisible
        ) {
          return true;
        }
      }
    }
    return false;
  }, [
    activeViewConfig,
    extraFilters,
    dateFilter,
    cellHeight,
    showErrors,
    showNonAnnotated,
    hasEvalFilter,
    columns,
  ]);

  const canSaveViewDeferred = useDeferredValue(canSaveView);

  // Update mutations for the explicit Save view button. Project-scoped on
  // ObservePage's Users fixed tab; workspace-scoped (USERS_TAB_TYPE) on the
  // top-level /dashboard/users page rendered by UserList.
  const { mutate: updateSavedView } = useUpdateSavedView(observeId);
  const { mutate: updateWorkspaceSavedView } =
    useUpdateWorkspaceSavedView(USERS_TAB_TYPE);

  // Active saved-view id from URL — "tab" key on ObservePage, "usersTab" on
  // UserList. Re-derived when the active config flips.
  const activeViewTabId = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    const key = isObservePath
      ? params.get("tab")
      : params.get("usersTab");
    return key?.startsWith("view-") ? key.slice(5) : null;
  }, [activeViewConfig, isObservePath]);

  const handleSaveView = useCallback(() => {
    if (!activeViewTabId) return;
    const config = getConfig();
    const mutate = isObservePath ? updateSavedView : updateWorkspaceSavedView;
    mutate(
      { id: activeViewTabId, config },
      {
        onSuccess: (response) => {
          // Refresh context baseline (Observe path) — UserList path's
          // activeViewConfig prop refreshes via the mutation's optimistic
          // setQueryData on the workspace cache.
          setActiveViewConfig(response?.data?.result?.config ?? config);
          enqueueSnackbar("View updated", { variant: "success" });
        },
        onError: () =>
          enqueueSnackbar("Failed to update view", { variant: "error" }),
      },
    );
  }, [
    activeViewTabId,
    getConfig,
    isObservePath,
    updateSavedView,
    updateWorkspaceSavedView,
    setActiveViewConfig,
  ]);

  // Register with ObserveHeaderContext so ObserveTabBar's "+" save flow can
  // snapshot the current Users config when the user is on this fixed tab —
  // without this, the save POSTs `config: {}` (TH-4578).
  useEffect(() => {
    registerGetViewConfig(getConfig);
    return () => registerGetViewConfig(null);
  }, [registerGetViewConfig, getConfig]);

  useEffect(() => {
    registerGetTabType(() => "users");
    return () => registerGetTabType(null);
  }, [registerGetTabType]);

  // Apply a saved view's config when activeViewConfig changes. Reuses the
  // existing applyConfig — handles extraFilters, dateFilter, display state,
  // column visibility.
  // Dep array intentionally only watches `activeViewConfig`. `applyConfig`'s
  // identity changes whenever `columns` does, and `applyConfig` itself
  // mutates `columns` via `updateColumnVisibility` — keeping it in deps
  // creates an infinite re-apply loop.
  useEffect(() => {
    if (!activeViewConfig) return;
    applyConfig(activeViewConfig);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeViewConfig]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      clearSelection();
      resetStore();
    };
  }, [clearSelection, resetStore]);

  const shouldShowEmptyLayout =
    hasData === false &&
    !isLoading &&
    searchState === "empty" &&
    !hasActiveFilter;

  const shouldShowGrid =
    hasData === true ||
    (isLoading && searchState !== "empty") ||
    searchState === "searching" ||
    hasActiveFilter;

  const shouldShowLoading = isLoading && hasData === null;

  return (
    <>
      {!observeId && (
        <Helmet>
          <title>Users</title>
        </Helmet>
      )}

      {/* ObserveToolbar — portals into tab bar */}
      <ObserveToolbar
        mode="users"
        // Date
        dateLabel={getDateLabel(dateFilter)}
        dateFilter={dateFilter}
        setDateFilter={setDateFilter}
        // Filter
        hasActiveFilter={hasActiveFilter}
        canSaveView={canSaveViewDeferred}
        onSaveView={handleSaveView}
        graphFilters={extraFilters}
        isFilterOpen={isFilterOpen}
        onFilterToggle={() => setIsFilterOpen(!isFilterOpen)}
        filterFields={USER_FILTER_FIELDS}
        onApplyExtraFilters={setExtraFilters}
        // Columns (Display panel)
        columns={columns}
        onColumnVisibilityChange={(e) => {
          setColumnConfigureAnchor(e?.currentTarget || null);
        }}
        setColumns={setColumns}
        onAutoSize={handleAutoSize}
        autoSizeAllCols={autoSizeAllCols}
        onAddCustomColumn={() => setOpenCustomColumnDialog(true)}
        // Row height
        cellHeight={cellHeight}
        setCellHeight={setCellHeight}
        // Metrics
        showErrors={showErrors}
        onToggleErrors={() => setShowErrors(!showErrors)}
        showNonAnnotated={showNonAnnotated}
        onToggleNonAnnotated={() => setShowNonAnnotated(!showNonAnnotated)}
        hasEvalFilter={hasEvalFilter}
        onToggleEvalFilter={() => setHasEvalFilter(!hasEvalFilter)}
        showEvalToggle
        // Compare
        isCompareActive={showCompare}
        onCompareToggle={() => setShowCompare(!showCompare)}
        // Group
        groupBy="users"
        onGroupByChange={
          observeId
            ? (key) => {
                switch (key) {
                  case "none":
                  case "trace":
                    navigate(`/dashboard/observe/${observeId}/llm-tracing`);
                    break;
                  case "span": {
                    const params = new URLSearchParams({
                      selectedTab: "spans",
                    });
                    navigate({
                      pathname: `/dashboard/observe/${observeId}/llm-tracing`,
                      search: `?${params}`,
                    });
                    break;
                  }
                  case "sessions":
                    navigate(`/dashboard/observe/${observeId}/sessions`);
                    break;
                  default:
                    break;
                }
              }
            : undefined
        }
      />

      {/* Filter chips. Inject `display_name` so chips render the column's
          human-readable label instead of the raw snake_case / UUID id. */}
      <FilterChips
        extraFilters={extraFilters.map((f) => ({
          ...f,
          display_name:
            f.display_name ||
            USER_FILTER_FIELDS.find((c) => c.id === f.column_id)?.name,
        }))}
        onRemoveFilter={(idx) => {
          setExtraFilters((prev) => prev.filter((_, i) => i !== idx));
        }}
        onClearAll={() => setExtraFilters([])}
      />

      {/* Graph — hidden in cross-project mode (no project context to
          aggregate metrics over) */}
      {observeId && (
        <Box sx={{ px: 2 }}>
          <Suspense fallback={null}>
            <PrimaryGraph
              filters={finalFilters}
              dateFilter={dateFilter}
              graphEndpoint={endpoints.project.getUsersAggregateGraphData()}
              defaultMetric="latency"
              graphLabel="User Metrics"
              trafficLabel="users"
            />
          </Suspense>
        </Box>
      )}

      {/* Content */}
      <Box
        sx={{
          backgroundColor: "background.paper",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          px: 2,
          pt: 1,
        }}
      >
        {/* Loading spinner */}
        {shouldShowLoading && (
          <Box
            sx={{
              flex: 1,
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
            }}
          >
            <CircularProgress />
          </Box>
        )}

        {/* Empty state */}
        {shouldShowEmptyLayout && (
          <Box
            sx={{
              flex: 1,
              display: "flex",
              justifyContent: "center",
            }}
          >
            <UsersEmptyScreen />
          </Box>
        )}

        {/* Grid */}
        {shouldShowGrid && (
          <Box sx={{ flex: 1, display: "flex", flexDirection: "column" }}>
            <UsersGrid
              setHasData={setHasData}
              setIsLoading={setIsLoading}
              setSearchState={setSearchState}
              hasActiveFilter={hasActiveFilter}
              cellHeight={cellHeight}
            />
          </Box>
        )}
      </Box>

      {/* Column visibility popover */}
      <ColumnConfigureDropDown
        open={openColumnConfigure}
        onClose={() => setColumnConfigureAnchor(null)}
        anchorEl={columnConfigureAnchor}
        columns={columns}
        setColumns={setColumns}
        onColumnVisibilityChange={updateColumnVisibility}
        useGrouping
      />

      {/* Custom columns dialog */}
      <CustomColumnDialog
        open={openCustomColumnDialog}
        onClose={() => setOpenCustomColumnDialog(false)}
        attributes={attributes}
        existingColumns={columns}
        onAddColumns={addCustomColumns}
        onRemoveColumns={removeCustomColumns}
      />
    </>
  );
};

UsersView.propTypes = {
  savedViewApiRef: PropTypes.shape({ current: PropTypes.any }),
  activeViewConfig: PropTypes.object,
};

export default UsersView;
