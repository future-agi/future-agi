import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React, {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useParams, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { Helmet } from "react-helmet-async";
import { formatDate } from "src/utils/report-utils";
import { endOfToday, sub } from "date-fns";
import { Events, trackEvent } from "src/utils/Mixpanel";
import { useUrlState } from "src/routes/hooks/use-url-state";
import { useObserveHeader } from "src/sections/project/context/ObserveHeaderContext";
// Shared observe components
import ObserveToolbar from "../LLMTracing/ObserveToolbar";
import FilterChips from "../LLMTracing/FilterChips";
import { useLLMTracingFilters } from "../LLMTracing/useLLMTracingFilters";
import { buildAddEvalsDraft } from "../LLMTracing/buildAddEvalsDraft";
import SelectAllBanner from "../LLMTracing/SelectAllBanner";

// Lazy-load graph
const PrimaryGraph = lazy(
  () => import("../LLMTracing/GraphSection/PrimaryGraph"),
);
const AddToQueueDialog = lazy(
  () =>
    import("src/sections/annotations/queues/components/add-to-queue-dialog"),
);

const SESSION_BULK_ACTIONS = [
  {
    id: "replay",
    label: "Replay Sessions",
    icon: "mdi:play-outline",
  },
  {
    id: "annotation-queue",
    label: "Add to annotation queue",
    icon: "mdi:clipboard-list-outline",
  },
];

// Session-specific
import SessionGrid from "./Session-grid";
import { initialVisibility } from "./common";
import { REPLAY_MODULES } from "./ReplaySessions/configurations";
import {
  useReplaySessionsStoreShallow,
  useSessionsGridStore,
  useSessionsGridStoreShallow,
} from "./ReplaySessions/store";
import { REPLAY_TYPES } from "./ReplaySessions/constants";
import { useCreateReplaySessions } from "src/api/project/replay-sessions";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import ColumnConfigureDropDown from "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown";
import useProjectFilterField from "../UsersView/useProjectFilterField";
import CustomColumnDialog from "../LLMTracing/CustomColumnDialog";

// ---------------------------------------------------------------------------
// Base session filter fields (always available)
// ---------------------------------------------------------------------------
const BASE_SESSION_FILTER_FIELDS = [
  { id: "session_id", name: "Session ID", category: "system", type: "string" },
  {
    id: "first_message",
    name: "First Message",
    category: "system",
    type: "string",
  },
  {
    id: "last_message",
    name: "Last Message",
    category: "system",
    type: "string",
  },
  { id: "user_id", name: "User ID", category: "system", type: "string" },
  { id: "duration", name: "Duration", category: "system", type: "number" },
  { id: "total_cost", name: "Total Cost", category: "system", type: "number" },
  {
    id: "total_traces_count",
    name: "Total Traces",
    category: "system",
    type: "number",
  },
];

// Build filter fields dynamically from session columns + base fields
const buildSessionFilterFields = (sessionColumns) => {
  const baseIds = new Set(BASE_SESSION_FILTER_FIELDS.map((f) => f.id));
  const extra = (sessionColumns || [])
    .filter((col) => !baseIds.has(col.id) && col.name)
    .map((col) => {
      const category =
        col.groupBy === "Annotation Metrics"
          ? "annotation"
          : col.groupBy === "Evaluation Metrics"
            ? "eval"
            : "system";
      return {
        id: col.id,
        name: col.name,
        category,
        type: col.dataType === "number" ? "number" : "string",
      };
    });
  return [...BASE_SESSION_FILTER_FIELDS, ...extra];
};

// Default filter and date range
const defaultFilterBase = [
  {
    columnId: "",
    filterConfig: { filterType: "", filterOp: "", filterValue: "" },
  },
];

const getDefaultDateRange = () => ({
  dateFilter: [
    formatDate(sub(new Date(), { months: 6 })),
    formatDate(endOfToday()),
  ],
  dateOption: "6M",
});

// Date label helper — mirrors LLMTracingView so the toolbar button reflects
// the restored URL state (shows picked dates for Custom, "Past N" for presets).
const PRESET_DATE_LABELS = {
  Today: "Today",
  Yesterday: "Yesterday",
  "7D": "Past 7D",
  "30D": "Past 30D",
  "3M": "Past 3M",
  "6M": "Past 6M",
  "12M": "Past 12M",
  "30 mins": "Past 30 mins",
  "6 hrs": "Past 6 hrs",
};

export const getDateLabel = (dateFilter) => {
  const option = dateFilter?.dateOption;
  if (option && option !== "Custom") {
    return PRESET_DATE_LABELS[option] || `Past ${option}`;
  }
  const dates = dateFilter?.dateFilter;
  if (!dates || dates.length < 2) return "Past 6M";
  const start = new Date(dates[0]);
  const end = new Date(dates[1]);
  if (isNaN(start.getTime()) || isNaN(end.getTime())) return "Past 6M";
  return `${start.toLocaleDateString()} - ${end.toLocaleDateString()}`;
};

// No-op extra properties for session filters (no reverse eval logic needed)
const noopExtraProperties = () => ({});

const SessionsView = ({ mode = "project", userIdForUserMode = null }) => {
  const isUserMode = mode === "user";
  const { observeId: routeObserveId } = useParams();
  const observeId = isUserMode ? null : routeObserveId;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const sessionGridApiRef = useRef(null);
  const { setHeaderConfig } = useObserveHeader();

  // --- Filter & date state (reuse trace filter hook) ---
  const defaultDateFilter = useMemo(() => getDefaultDateRange(), []);
  const [sessionColumns, setSessionColumns] = useState([]);

  const { validatedFilters, setDateFilter, dateFilter } = useLLMTracingFilters(
    defaultFilterBase,
    defaultDateFilter,
    "sessionFilter",
    "sessionDateFilter",
    sessionColumns,
    noopExtraProperties,
  );

  // --- Extra filters from TraceFilterPanel (popover) ---
  const [extraFilters, setExtraFilters] = useState([]);
  const [isFilterOpen, setIsFilterOpen] = useUrlState(
    "sessionFilterOpen",
    false,
  );

  const hasActiveFilter = extraFilters.length > 0;

  const handleAddEvals = useCallback(() => {
    const url = buildAddEvalsDraft({
      observeId,
      rowType: "sessions",
      mainFilters: [],
      extraFilters,
      dateFilter,
    });
    navigate(url);
  }, [observeId, extraFilters, dateFilter, navigate]);

  // --- Display panel state ---
  const [showCompare, setShowCompare] = useUrlState(
    "sessionShowCompare",
    false,
  );

  // In user mode expose an extra "Project" filter so the user can narrow
  // the cross-project session list to a subset of projects.
  const projectFilterField = useProjectFilterField({ enabled: isUserMode });
  const sessionFilterFields = useMemo(
    () =>
      projectFilterField
        ? [...BASE_SESSION_FILTER_FIELDS, projectFilterField]
        : BASE_SESSION_FILTER_FIELDS,
    [projectFilterField],
  );
  const filterChipLabelMap = useMemo(() => {
    if (!projectFilterField?.choices?.length) return undefined;
    return {
      project_id: Object.fromEntries(
        projectFilterField.choices.map((c) => [c.value, c.label]),
      ),
    };
  }, [projectFilterField]);

  // In user mode every grid is scoped by user_id. Inject a structural
  // filter that prepends to the validated filter list (same pattern
  // used by LLMTracingView).
  const userScopeFilter = useMemo(
    () =>
      isUserMode && userIdForUserMode
        ? [
            {
              columnId: "user_id",
              filterConfig: {
                filterType: "text",
                filterOp: "equals",
                filterValue: userIdForUserMode,
              },
            },
          ]
        : [],
    [isUserMode, userIdForUserMode],
  );

  // Combine validated filters with extra filters
  // extraFilters from ObserveToolbar use snake_case keys (column_id, filter_config)
  // validatedFilters from useLLMTracingFilters use camelCase keys (columnId, filterConfig)
  // Normalize extra filters to camelCase so objectCamelToSnake in Session-grid handles them uniformly
  const finalFilters = useMemo(() => {
    const base = [...userScopeFilter, ...validatedFilters];
    if (!extraFilters.length) return base;
    const normalized = extraFilters.map((f) => ({
      columnId: f.column_id || f.columnId || "",
      filterConfig: {
        filterType:
          f.filter_config?.filter_type || f.filterConfig?.filterType || "text",
        filterOp:
          f.filter_config?.filter_op || f.filterConfig?.filterOp || "equals",
        filterValue:
          f.filter_config?.filter_value || f.filterConfig?.filterValue || "",
        ...(f.filter_config?.col_type && { colType: f.filter_config.col_type }),
      },
    }));
    return [...base, ...normalized];
  }, [userScopeFilter, validatedFilters, extraFilters]);

  // --- Column visibility ---
  const [updateObj, setUpdateObj] = useState(initialVisibility);
  const [autoSizeOn, setAutoSizeOn] = useState(false);

  const { mutate: updateSessionListColumnVisibility } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.project.updateSessionListColumnVisibility(), {
        project_id: observeId,
        visibility: data,
      }),
  });

  const onSessionVisibilityColumnChange = useCallback(
    (newUpdateObj) => {
      setUpdateObj(newUpdateObj);
      setSessionColumns((cols) =>
        cols.map((col) => ({ ...col, isVisible: newUpdateObj[col.id] })),
      );
      updateSessionListColumnVisibility(newUpdateObj);
    },
    [updateSessionListColumnVisibility],
  );

  // --- Row height ---
  const [cellHeight, setCellHeight] = useUrlState("sessionCellHeight", "Short");

  // --- Replay sessions ---
  const {
    openReplaySessionDrawer,
    setReplayType,
    setOpenReplaySessionDrawer,
    setCreatedReplay,
  } = useReplaySessionsStoreShallow((s) => ({
    openReplaySessionDrawer: s.openReplaySessionDrawer,
    setReplayType: s.setReplayType,
    setOpenReplaySessionDrawer: s.setOpenReplaySessionDrawer,
    setCreatedReplay: s.setCreatedReplay,
  }));

  const { totalRowCount, toggledNodes, selectAll } =
    useSessionsGridStoreShallow((s) => ({
      totalRowCount: s.totalRowCount,
      toggledNodes: s.toggledNodes,
      selectAll: s.selectAll,
    }));

  const { mutate: createReplaySessions } = useCreateReplaySessions();

  const onSelectionChanged = useCallback((params) => {
    const ssState =
      typeof params.api.getServerSideSelectionState === "function"
        ? params.api.getServerSideSelectionState() || {}
        : {};
    const nodes = params.api.getSelectedNodes?.() || [];
    const idsFromNodes = nodes
      .map((n) => n.data?.session_id || n.data?.id)
      .filter(Boolean);
    const toggled =
      Array.isArray(ssState.toggledNodes) && ssState.toggledNodes.length > 0
        ? ssState.toggledNodes
        : idsFromNodes;
    useSessionsGridStore.setState({
      toggledNodes: toggled,
      selectAll: !!ssState.selectAll,
      totalRowCount: params.api.totalRowCount,
    });
  }, []);

  const selectedCount = selectAll
    ? totalRowCount - toggledNodes.length
    : toggledNodes.length;

  const [queueAnchorEl, setQueueAnchorEl] = useState(null);
  // Opt-in for filter-mode session bulk add. Set to true when
  // the user clicks the banner's "Select all N matching your filter" link.
  // Resets when the grid clears select-all, when the project changes, or
  // when the filter payload changes (the opt-in no longer matches the view).
  const [sessionFilterSelectionMode, setSessionFilterSelectionMode] =
    useState(false);
  useEffect(() => {
    if (!selectAll) setSessionFilterSelectionMode(false);
  }, [selectAll]);
  useEffect(() => {
    setSessionFilterSelectionMode(false);
  }, [observeId]);
  useEffect(() => {
    setSessionFilterSelectionMode(false);
  }, [finalFilters]);

  const handleBulkAction = useCallback(
    (action, event) => {
      if (action === "replay") {
        const replayData = {
          project_id: observeId,
          replay_type: REPLAY_MODULES.SESSIONS,
          ids: toggledNodes,
          select_all: selectAll,
        };
        createReplaySessions(replayData, {
          onSuccess: (data) => {
            setCreatedReplay(data?.data?.result);
            setReplayType(REPLAY_TYPES.NEW_GROUP);
            setOpenReplaySessionDrawer(REPLAY_MODULES?.SESSIONS, true);
          },
          onError: () => {
            enqueueSnackbar("Failed to start replay", { variant: "error" });
          },
        });
      } else if (action === "annotation-queue") {
        // With filter-mode opt-in (the SelectAllBanner), the popover's
        // submit posts `{selection: {mode: "filter", ...}}` to the Phase 6
        // backend endpoint. Without opt-in under select-all we still bail
        // out — toggledNodes holds the *deselected* rows in that mode and
        // an enumerated add would be wrong.
        if (selectAll && !sessionFilterSelectionMode) {
          enqueueSnackbar(
            "Use the 'Select all matching your filter' banner to add the full set, or deselect 'all' and pick specific rows.",
            { variant: "info" },
          );
          return;
        }
        setQueueAnchorEl(event?.currentTarget || null);
      }
    },
    [
      observeId,
      toggledNodes,
      selectAll,
      sessionFilterSelectionMode,
      createReplaySessions,
      setCreatedReplay,
      setReplayType,
      setOpenReplaySessionDrawer,
    ],
  );

  // --- Refresh ---
  const refreshSessions = useCallback(() => {
    trackEvent(Events.pObserveRefreshClicked);
    if (sessionGridApiRef.current) {
      sessionGridApiRef.current.api.refreshServerSide();
    }
    queryClient.invalidateQueries({ queryKey: ["session-list"] });
  }, [queryClient]);

  // --- Auto-size columns ---
  const handleAutoSize = useCallback(() => {
    if (!sessionGridApiRef?.current?.api) return;
    const gridApi = sessionGridApiRef.current.api;
    const allColumnIds = [];
    gridApi.getColumnDefs()?.forEach((column) => {
      if (column?.field) allColumnIds.push(column.field);
    });
    if (!autoSizeOn) {
      setAutoSizeOn(true);
      gridApi.autoSizeColumns(allColumnIds, false);
    } else {
      setAutoSizeOn(false);
      gridApi.sizeColumnsToFit();
    }
  }, [autoSizeOn]);

  // --- Header config ---
  useEffect(() => {
    // In user mode the page lives outside the observe shell — the parent
    // page (CrossProjectUserDetailPage) renders its own header.
    if (isUserMode) return;
    setHeaderConfig((prev) => ({
      ...prev,
      text: "Sessions",
      filterSession: finalFilters,
      refreshData: refreshSessions,
    }));
  }, [isUserMode, finalFilters, refreshSessions, setHeaderConfig]);

  // --- Grid disable when replay drawer is open ---
  const shouldDisable = useMemo(() => {
    return openReplaySessionDrawer[REPLAY_MODULES.SESSIONS];
  }, [openReplaySessionDrawer]);

  const onGridReady = useCallback(
    (params) => {
      sessionGridApiRef.current = params;
      setHeaderConfig((prev) => ({ ...prev, gridApi: params.api }));
    },
    [setHeaderConfig],
  );

  // --- Column config for display panel ---
  const displayColumns = useMemo(() => {
    return sessionColumns.map((col) => ({
      ...col,
      isVisible: updateObj[col.id] ?? true,
    }));
  }, [sessionColumns, updateObj]);

  // --- Custom columns ---
  const [openCustomColumn, setOpenCustomColumn] = useState(false);
  const pendingCustomColumnsRef = useRef([]);

  const { data: evalAttributes } = useQuery({
    queryKey: ["eval-attributes", observeId],
    queryFn: () =>
      axios.get(endpoints.project.getEvalAttributeList(), {
        params: { filters: JSON.stringify({ project_id: observeId }) },
      }),
    select: (data) => data.data?.result,
    enabled: Boolean(observeId),
  });
  const attributes = useMemo(() => evalAttributes || [], [evalAttributes]);

  const handleAddCustomColumns = useCallback((newCols) => {
    setSessionColumns((prev) => {
      const existingIds = new Set((prev || []).map((c) => c.id));
      const deduped = newCols.filter((c) => !existingIds.has(c.id));
      return [...(prev || []), ...deduped];
    });
  }, []);

  const handleRemoveCustomColumns = useCallback((idsToRemove) => {
    const removeSet = new Set(idsToRemove || []);
    setSessionColumns((prev) =>
      (prev || []).filter(
        (c) => !(c.groupBy === "Custom Columns" && removeSet.has(c.id)),
      ),
    );
  }, []);

  // --- Column configure dropdown ---
  const [openColumnConfigure, setOpenColumnConfigure] = useState(false);
  const columnConfigureRef = useRef(null);

  return (
    <>
      <Helmet>
        <title>Observe - Sessions</title>
      </Helmet>

      {/* ObserveToolbar — portals into tab bar */}
      <ObserveToolbar
        mode="sessions"
        // Date
        dateLabel={getDateLabel(dateFilter)}
        dateFilter={dateFilter}
        setDateFilter={setDateFilter}
        // Filter
        hasActiveFilter={hasActiveFilter}
        isFilterOpen={isFilterOpen}
        onFilterToggle={() => setIsFilterOpen(!isFilterOpen)}
        filterFields={sessionFilterFields}
        onApplyExtraFilters={setExtraFilters}
        // Columns
        columns={displayColumns}
        onColumnVisibilityChange={(e) => {
          columnConfigureRef.current = e?.currentTarget || e?.target;
          setOpenColumnConfigure(true);
        }}
        onAutoSize={handleAutoSize}
        autoSizeAllCols={autoSizeOn}
        // Row height
        cellHeight={cellHeight}
        setCellHeight={setCellHeight}
        // Compare
        isCompareActive={showCompare}
        onCompareToggle={() => setShowCompare(!showCompare)}
        // Group
        groupBy="sessions"
        hiddenGroupByOptions={isUserMode ? ["users"] : []}
        // User mode: trace/span take the user back into LLMTracingView via
        // the user-detail URL. Project mode: cross-nav into observe routes.
        onGroupByChange={(key) => {
          if (isUserMode) {
            if (key !== "none" && key !== "trace" && key !== "span") return;
            const params = new URLSearchParams({ userTab: "traces" });
            if (key === "span") params.set("selectedTab", "spans");
            navigate({
              pathname: `/dashboard/users/${encodeURIComponent(
                userIdForUserMode,
              )}`,
              search: `?${params}`,
            });
            return;
          }
          switch (key) {
            case "none":
            case "trace":
              navigate(`/dashboard/observe/${observeId}/llm-tracing`);
              break;
            case "span": {
              const params = new URLSearchParams({ selectedTab: "spans" });
              navigate({
                pathname: `/dashboard/observe/${observeId}/llm-tracing`,
                search: `?${params}`,
              });
              break;
            }
            case "users":
              navigate(`/dashboard/observe/${observeId}/users`);
              break;
            default:
              break;
          }
        }}
        // Bulk actions
        selectedCount={selectedCount}
        allMatching={sessionFilterSelectionMode}
        onClearSelection={() => {
          sessionGridApiRef.current?.api?.deselectAll();
          useSessionsGridStore.setState({
            toggledNodes: [],
            selectAll: false,
          });
        }}
        onBulkAction={handleBulkAction}
        bulkActions={SESSION_BULK_ACTIONS}
        onAddEvals={handleAddEvals}
        onAddCustomColumn={() => setOpenCustomColumn(true)}
      />

      <Suspense fallback={null}>
        <AddToQueueDialog
          anchorEl={queueAnchorEl}
          onClose={() => setQueueAnchorEl(null)}
          sourceType="trace_session"
          sourceIds={
            sessionFilterSelectionMode
              ? toggledNodes || []
              : (toggledNodes || []).filter(Boolean)
          }
          itemName="Session"
          selectionMode={sessionFilterSelectionMode ? "filter" : "manual"}
          filter={sessionFilterSelectionMode ? finalFilters : null}
          projectId={sessionFilterSelectionMode ? observeId : null}
          onSuccess={() => {
            setSessionFilterSelectionMode(false);
            sessionGridApiRef.current?.api?.deselectAll();
            useSessionsGridStore.setState({
              toggledNodes: [],
              selectAll: false,
            });
          }}
        />
      </Suspense>

      {/* Filter chips. Inject `display_name` so chips render the column's
          human-readable label instead of the raw snake_case / UUID id. */}
      <FilterChips
        extraFilters={extraFilters.map((f) => ({
          ...f,
          display_name:
            f.display_name ||
            sessionFilterFields.find((c) => c.id === f.column_id)?.name,
        }))}
        fieldLabelMap={filterChipLabelMap}
        onRemoveFilter={(idx) => {
          setExtraFilters((prev) => prev.filter((_, i) => i !== idx));
        }}
        onClearAll={() => setExtraFilters([])}
      />

      {/* Graph — hidden in user mode (no project context) */}
      {!isUserMode && (
        <Box sx={{ px: 2 }}>
          <Suspense fallback={null}>
            <PrimaryGraph
              filters={finalFilters}
              dateFilter={dateFilter}
              graphEndpoint={endpoints.project.getSessionGraphData()}
              defaultMetric="latency"
              graphLabel="Session Metrics"
              trafficLabel="sessions"
            />
          </Suspense>
        </Box>
      )}

      <SelectAllBanner
        visible={selectAll && !sessionFilterSelectionMode}
        visibleCount={
          sessionGridApiRef.current?.api?.getDisplayedRowCount?.() || 0
        }
        totalMatching={totalRowCount || 0}
        noun="session"
        onSelectAll={() => setSessionFilterSelectionMode(true)}
      />

      {/* Content */}
      <Box
        display="flex"
        flexDirection="column"
        sx={{ flex: 1, height: "100%" }}
      >
        <SessionGrid
          columns={sessionColumns}
          setColumns={setSessionColumns}
          ref={sessionGridApiRef}
          updateObj={updateObj}
          filters={finalFilters}
          projectId={observeId}
          cellHeight={cellHeight}
          onSelectionChanged={onSelectionChanged}
          className={shouldDisable ? "ag-grid-disabled" : ""}
          onGridReady={onGridReady}
          pendingCustomColumnsRef={pendingCustomColumnsRef}
        />
      </Box>

      {/* Column configure dropdown */}
      <ColumnConfigureDropDown
        open={openColumnConfigure}
        onClose={() => setOpenColumnConfigure(false)}
        anchorEl={columnConfigureRef?.current}
        columns={sessionColumns}
        onColumnVisibilityChange={onSessionVisibilityColumnChange}
        setColumns={setSessionColumns}
        defaultGrouping="Session Columns"
      />

      <CustomColumnDialog
        open={openCustomColumn}
        onClose={() => setOpenCustomColumn(false)}
        attributes={attributes}
        existingColumns={sessionColumns}
        onAddColumns={handleAddCustomColumns}
        onRemoveColumns={handleRemoveCustomColumns}
      />
    </>
  );
};

SessionsView.propTypes = {
  mode: PropTypes.oneOf(["project", "user"]),
  userIdForUserMode: PropTypes.string,
};

export default SessionsView;
