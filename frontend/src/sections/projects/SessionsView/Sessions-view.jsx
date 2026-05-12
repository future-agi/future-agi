import { Box } from "@mui/material";
import PropTypes from "prop-types";
import {
  useUpdateSavedView,
  useUpdateWorkspaceSavedView,
} from "src/api/project/saved-views";

const USER_DETAIL_TAB_TYPE = "user_detail";
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
import { filtersContentEqual } from "../saved-view-utils";

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
  const {
    setHeaderConfig,
    activeViewConfig,
    setActiveViewConfig,
    registerGetViewConfig,
    registerGetTabType,
  } = useObserveHeader();

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

  // Snapshot of updateObj at the moment a saved view becomes active, so
  // canSaveView can compare against this snapshot for views whose stored
  // config doesn't include `display.visibleColumns` (older views saved
  // before that field was being captured).
  const viewLoadedUpdateObjRef = useRef(null);

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
      // Only persist project-wide default visibility when actually scoped to
      // a project (not user mode — observeId is null there, and the request
      // 400s with "Project not found") and not on a saved view tab. On a
      // saved view, the per-view config owns its own visibleColumns and gets
      // persisted via the explicit Save view button.
      // Inline URL parse rather than closing over activeViewTabId (declared
      // further down in the file) to avoid a TDZ on the deps array.
      const params = new URLSearchParams(window.location.search);
      const tabKey = isUserMode ? params.get("userTab") : params.get("tab");
      const onSavedView = tabKey?.startsWith("view-");
      if (!isUserMode && !onSavedView) {
        updateSessionListColumnVisibility(newUpdateObj);
      }
    },
    [updateSessionListColumnVisibility, isUserMode],
  );

  // --- Row height ---
  const [cellHeight, setCellHeight] = useUrlState("sessionCellHeight", "Short");

  // "Save view" only surfaces on a custom saved view when the user has
  // modified its state. On a default tab the "+" button handles save-as-new,
  // so we keep Save view out of the toolbar there.
  const canSaveView = useMemo(() => {
    if (!activeViewConfig) return false;

    const baselineExtraFilters = activeViewConfig.extraFilters || [];
    const baselineDisplay = activeViewConfig.display || {};
    const baselineDateOption =
      baselineDisplay.dateFilter?.dateOption ?? null;

    if (!filtersContentEqual(extraFilters, baselineExtraFilters)) return true;
    if ((dateFilter?.dateOption ?? null) !== baselineDateOption) return true;
    if (
      baselineDisplay.cellHeight !== undefined &&
      baselineDisplay.cellHeight !== cellHeight
    ) {
      return true;
    }
    if (
      baselineDisplay.showCompare !== undefined &&
      baselineDisplay.showCompare !== showCompare
    ) {
      return true;
    }
    // Column visibility — prefer the saved baseline (`display.visibleColumns`),
    // fall back to the snapshot taken when the view was loaded so older views
    // that never persisted visibleColumns still detect toggles.
    const baseline =
      baselineDisplay.visibleColumns &&
      typeof baselineDisplay.visibleColumns === "object"
        ? baselineDisplay.visibleColumns
        : viewLoadedUpdateObjRef.current;
    if (baseline && updateObj && typeof updateObj === "object") {
      for (const colId of Object.keys(baseline)) {
        const cur = updateObj[colId];
        if (cur !== undefined && cur !== baseline[colId]) {
          return true;
        }
      }
    }
    // Custom columns: compare sorted id lists. Without this, adding or
    // removing a custom col on a saved view leaves the Save view button
    // disabled — users have no way to persist the change.
    const currentCustomIds = (sessionColumns || [])
      .filter((c) => c.groupBy === "Custom Columns")
      .map((c) => c.id)
      .sort();
    const baselineCustomIds = (
      Array.isArray(baselineDisplay.customColumns)
        ? baselineDisplay.customColumns
        : []
    )
      .map((c) => c.id)
      .sort();
    if (currentCustomIds.length !== baselineCustomIds.length) return true;
    for (let i = 0; i < currentCustomIds.length; i += 1) {
      if (currentCustomIds[i] !== baselineCustomIds[i]) return true;
    }
    return false;
  }, [
    activeViewConfig,
    extraFilters,
    dateFilter,
    cellHeight,
    showCompare,
    updateObj,
    sessionColumns,
  ]);

  // Defer so the button doesn't flicker during the one-render gap between
  // filter state updating urgently and activeViewConfig catching up from
  // startTransition in the apply path.
  const canSaveViewDeferred = useDeferredValue(canSaveView);

  // --- Saved view capture + apply (TH-4578) ---
  // Build a view-config snapshot that mirrors LLMTracingView's shape. dateFilter
  // stays inside `display` because the backend's saved-view serializer only
  // whitelists `display` for arbitrary sub-keys (no top-level `dateFilter`).
  // Note: in earlier versions this view skipped writing URL-synced state in
  // the apply effect on the assumption that a click-time seedUrlForView
  // would populate the URL. That's only true on UserDetailTabBar — the
  // ObservePage tab-bar path doesn't seed sessionCellHeight / sessionShowCompare /
  // sessionDateFilter, so display options never made it onto the page when
  // selecting a sessions saved view from the project's tab bar. Now apply
  // pushes them into URL state directly (matching UsersView / LLMTracingView).
  const buildViewConfig = useCallback(() => {
    const columnState =
      sessionGridApiRef.current?.api?.getColumnState?.() ?? undefined;
    // updateObj is the authoritative {col.id: isVisible} source.
    const hasVisibility = updateObj && Object.keys(updateObj).length > 0;
    // Persist custom columns separately. Their colIds also appear in
    // columnState, but columnState alone can't recreate them — AG Grid
    // silently drops state for unknown colIds, so without this list the
    // custom cols never come back on restore.
    const customColumns = (sessionColumns || []).filter(
      (c) => c.groupBy === "Custom Columns",
    );
    return {
      display: {
        cellHeight,
        showCompare,
        dateFilter,
        ...(hasVisibility ? { visibleColumns: updateObj } : {}),
        ...(columnState ? { columnState } : {}),
        ...(customColumns.length > 0 ? { customColumns } : {}),
      },
      extraFilters: extraFilters || [],
    };
  }, [
    cellHeight,
    showCompare,
    dateFilter,
    extraFilters,
    updateObj,
    sessionColumns,
  ]);

  useEffect(() => {
    registerGetViewConfig(buildViewConfig);
    return () => registerGetViewConfig(null);
  }, [registerGetViewConfig, buildViewConfig]);

  useEffect(() => {
    registerGetTabType(() => "sessions");
    return () => registerGetTabType(null);
  }, [registerGetTabType]);

  // Update mutations for the explicit Save view button. Project-scoped on
  // ObservePage, workspace-scoped (user_detail) on CrossProjectUserDetailPage.
  const { mutate: updateSavedView } = useUpdateSavedView(observeId);
  const { mutate: updateWorkspaceSavedView } =
    useUpdateWorkspaceSavedView(USER_DETAIL_TAB_TYPE);

  // Active saved-view id from URL — "tab" key on ObservePage, "userTab" on
  // CrossProjectUserDetailPage. Re-derived when activeViewConfig flips.
  const activeViewTabId = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    const key = isUserMode ? params.get("userTab") : params.get("tab");
    return key?.startsWith("view-") ? key.slice(5) : null;
  }, [activeViewConfig, isUserMode]);

  // localStorage key for default-tab display state. Project-scoped on the
  // ObservePage path, user-scoped on CrossProjectUserDetailPage. Mirrors
  // the LLMTracingView / UsersView shape but with a `-sessions-` segment
  // so the three views don't collide on payload schema.
  const displayStorageKey = useMemo(
    () =>
      isUserMode
        ? `user-sessions-display-${userIdForUserMode}`
        : `observe-sessions-display-${observeId}`,
    [isUserMode, userIdForUserMode, observeId],
  );

  // Hydrate default-tab display state from localStorage. Custom cols go
  // through `pendingCustomColumnsRef` so Session-grid's first-fetch merge
  // drains them — same path the saved-view apply effect uses. The ref
  // gate fires the hydrate once per project/user; the `activeViewTabId`
  // check skips it on saved-view URLs where the view config owns state.
  const hydratedKeyRef = useRef(null);
  // Set immediately after a hydrate so the save effect skips its first
  // fire — that first fire would otherwise close over the PRE-hydrate
  // state (the hydrate's setters are queued, not yet committed to this
  // render) and persist defaults back to localStorage, clobbering the
  // values we just loaded.
  const skipNextSaveRef = useRef(false);
  useEffect(() => {
    if (activeViewTabId) return;
    if (hydratedKeyRef.current === displayStorageKey) return;
    hydratedKeyRef.current = displayStorageKey;
    try {
      const raw = localStorage.getItem(displayStorageKey);
      if (!raw) return;
      skipNextSaveRef.current = true;
      const saved = JSON.parse(raw);
      if (saved.cellHeight) setCellHeight(saved.cellHeight);
      if (typeof saved.showCompare === "boolean") {
        setShowCompare(saved.showCompare);
      }
      if (saved.visibleColumns && typeof saved.visibleColumns === "object") {
        setUpdateObj(saved.visibleColumns);
      }
      if (
        Array.isArray(saved.customColumns) &&
        saved.customColumns.length > 0
      ) {
        pendingCustomColumnsRef.current = saved.customColumns;
      }
    } catch {
      /* ignore corrupted localStorage */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [displayStorageKey]);

  // Persist display state on every change. Skip until the hydrate has
  // run for this key, otherwise the initial empty state would overwrite
  // saved customs before the load effect gets a chance to populate them.
  useEffect(() => {
    if (activeViewTabId) return;
    if (hydratedKeyRef.current !== displayStorageKey) return;
    if (skipNextSaveRef.current) {
      skipNextSaveRef.current = false;
      return;
    }
    const customColumns = (sessionColumns || []).filter(
      (c) => c.groupBy === "Custom Columns",
    );
    const payload = {
      cellHeight,
      showCompare,
      ...(updateObj && Object.keys(updateObj).length > 0
        ? { visibleColumns: updateObj }
        : {}),
      ...(customColumns.length > 0 ? { customColumns } : {}),
    };
    try {
      localStorage.setItem(displayStorageKey, JSON.stringify(payload));
    } catch {
      /* quota exceeded */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    displayStorageKey,
    cellHeight,
    showCompare,
    updateObj,
    sessionColumns,
  ]);

  const handleSaveView = useCallback(() => {
    if (!activeViewTabId) return;
    const config = buildViewConfig();
    const mutate = isUserMode ? updateWorkspaceSavedView : updateSavedView;
    mutate(
      { id: activeViewTabId, config },
      {
        onSuccess: (response) => {
          setActiveViewConfig(response?.data?.result?.config ?? config);
          enqueueSnackbar("View updated", { variant: "success" });
        },
        onError: () =>
          enqueueSnackbar("Failed to update view", { variant: "error" }),
      },
    );
  }, [
    activeViewTabId,
    buildViewConfig,
    isUserMode,
    updateSavedView,
    updateWorkspaceSavedView,
    setActiveViewConfig,
  ]);

  // Pending column state queued before the grid was ready. Drain effect
  // below applies it once `sessionGridApiRef.current.api` shows up.
  const pendingColumnStateRef = useRef(null);

  // Apply a saved view's config — push display options into URL-synced
  // state (matches UsersView / LLMTracingView). UserDetailTabBar still
  // pre-seeds the same URL keys at click time for snappiness; the writes
  // here are idempotent for that path and are the only source of truth
  // for the ObservePage tab-bar path, which doesn't pre-seed display.
  useEffect(() => {
    if (!activeViewConfig) {
      // Transitioning back to a default tab — wipe everything that was
      // applied by the saved view. URL-synced display state (cellHeight,
      // showCompare, dateFilter) reverts via useUrlState as the parent's
      // navigate() drops those URL keys; everything else has to be reset
      // here because it lives in plain useState or inside AG Grid.
      setExtraFilters((prev) => (prev.length === 0 ? prev : []));
      viewLoadedUpdateObjRef.current = null;
      setUpdateObj(initialVisibility);
      const api = sessionGridApiRef.current?.api;
      if (api?.setColumnsVisible) {
        const showIds = Object.keys(initialVisibility).filter(
          (id) => initialVisibility[id],
        );
        const hideIds = Object.keys(initialVisibility).filter(
          (id) => !initialVisibility[id],
        );
        if (showIds.length) api.setColumnsVisible(showIds, true);
        if (hideIds.length) api.setColumnsVisible(hideIds, false);
      }
      if (api?.resetColumnState) api.resetColumnState();
      pendingColumnStateRef.current = null;
      pendingCustomColumnsRef.current = [];
      // Strip saved-view custom cols from sessionColumns so they don't
      // linger on the default tab. Symmetric with LLMTracingView.
      setSessionColumns((prev) =>
        (prev || []).filter((c) => c.groupBy !== "Custom Columns"),
      );
      // Re-hydrate default-tab preferences from localStorage. The mount
      // hydrate effect is keyed on `displayStorageKey` and won't re-fire
      // on a same-project saved-view → default transition; without this,
      // going back to default after a saved view would land on factory
      // defaults instead of the user's preferences. Pending customs go
      // through the ref and drain on Session-grid's next merge.
      try {
        const raw = localStorage.getItem(displayStorageKey);
        if (raw) {
          // Mirror the mount-hydrate skip-flag so the save effect's next
          // fire doesn't write the pre-hydrate state back over localStorage.
          skipNextSaveRef.current = true;
          const saved = JSON.parse(raw);
          if (saved.cellHeight) setCellHeight(saved.cellHeight);
          if (typeof saved.showCompare === "boolean") {
            setShowCompare(saved.showCompare);
          }
          if (
            saved.visibleColumns &&
            typeof saved.visibleColumns === "object"
          ) {
            setUpdateObj(saved.visibleColumns);
          }
          if (
            Array.isArray(saved.customColumns) &&
            saved.customColumns.length > 0
          ) {
            pendingCustomColumnsRef.current = saved.customColumns;
            sessionGridApiRef.current?.api?.refreshServerSide?.({
              purge: true,
            });
          }
        }
      } catch {
        /* ignore corrupted localStorage */
      }
      return;
    }
    const display = activeViewConfig.display || {};
    if (display.cellHeight) setCellHeight(display.cellHeight);
    if (typeof display.showCompare === "boolean") {
      setShowCompare(display.showCompare);
    }
    if (display.dateFilter) {
      setDateFilter(display.dateFilter);
    }
    // Apply visibleColumns dict — `updateObj` is the single source of truth
    // for column visibility (drives the ColumnConfigure popover via
    // displayColumns and is the authoritative {col.id: bool} dict). Push
    // visibility into AG Grid directly when the api is available so the
    // grid display matches without waiting for a re-render. Done before the
    // snapshot below so canSaveView's baseline matches the just-applied state.
    if (
      display.visibleColumns &&
      typeof display.visibleColumns === "object"
    ) {
      const next = { ...display.visibleColumns };
      setUpdateObj(next);
      const api = sessionGridApiRef.current?.api;
      if (api?.setColumnsVisible) {
        const toShow = [];
        const toHide = [];
        Object.entries(next).forEach(([colId, visible]) => {
          (visible ? toShow : toHide).push(colId);
        });
        if (toShow.length) api.setColumnsVisible(toShow, true);
        if (toHide.length) api.setColumnsVisible(toHide, false);
      }
    }
    // Capture the visibility state at the moment this saved view is loaded,
    // so canSaveView can compare any subsequent toggles against this
    // snapshot — covers older views that didn't persist `visibleColumns`.
    // Use the just-applied dict if it exists, otherwise current updateObj.
    viewLoadedUpdateObjRef.current = display.visibleColumns
      ? { ...display.visibleColumns }
      : updateObj
        ? { ...updateObj }
        : null;
    // Strip existing customs from sessionColumns before queuing this
    // view's set — guards against view→view transitions and against
    // a default-tab→saved-view click where default customs would
    // otherwise stack on top of the saved view's. Symmetric with the
    // LLMTracingView apply effect.
    setSessionColumns((prev) =>
      (prev || []).filter((c) => c.groupBy !== "Custom Columns"),
    );
    // Queue the saved view's custom cols for the Session-grid merge to
    // drain into sessionColumns on its next fetch. Defer (don't push
    // directly) so the grid doesn't render with only-custom-col headers
    // before the standard backend cols land.
    const savedCustomCols = Array.isArray(display.customColumns)
      ? display.customColumns
      : [];
    pendingCustomColumnsRef.current = savedCustomCols;
    // Force a Session-grid re-fetch so its merge logic drains the queued
    // customs. Without this, when the saved view's extraFilters happen to
    // equal the current state (commonly both empty on a hard refresh),
    // setExtraFilters returns the same array ref via the optimization
    // below — finalFilters doesn't recompute, Session-grid's dataSource
    // memo doesn't recreate, and no fetch fires. The pending ref then
    // sits there forever and customs never appear.
    if (savedCustomCols.length > 0) {
      sessionGridApiRef.current?.api?.refreshServerSide?.({ purge: true });
    }
    if (Array.isArray(display.columnState) && display.columnState.length > 0) {
      // Always queue columnState when there are custom cols pending —
      // applying it immediately would race the Session-grid merge that
      // adds the customs to sessionColumns. AG Grid silently drops
      // applyColumnState entries for colIds it doesn't know about, so
      // widths/order for custom cols would be lost. The sessionColumns
      // drain effect below applies the queued state once the customs land.
      if (savedCustomCols.length > 0) {
        pendingColumnStateRef.current = display.columnState;
      } else {
        const api = sessionGridApiRef.current?.api;
        if (api?.applyColumnState) {
          api.applyColumnState({
            state: display.columnState,
            applyOrder: true,
          });
        } else {
          pendingColumnStateRef.current = display.columnState;
        }
      }
    }
    const nextExtraFilters = activeViewConfig.extraFilters || [];
    setExtraFilters((prev) => {
      if (prev.length === 0 && nextExtraFilters.length === 0) return prev;
      if (prev.length === nextExtraFilters.length) {
        const allSame = prev.every(
          (f, i) =>
            f?.column_id === nextExtraFilters[i]?.column_id &&
            JSON.stringify(f?.filter_config) ===
              JSON.stringify(nextExtraFilters[i]?.filter_config),
        );
        if (allSame) return prev;
      }
      return nextExtraFilters;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeViewConfig]);

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
      // Drain any saved-view columnState that arrived before the grid mounted.
      if (pendingColumnStateRef.current && params.api?.applyColumnState) {
        params.api.applyColumnState({
          state: pendingColumnStateRef.current,
          applyOrder: true,
        });
        pendingColumnStateRef.current = null;
      }
    },
    [setHeaderConfig],
  );

  // Drain pendingColumnState once sessionColumns updates (custom cols
  // landed via the Session-grid merge) and the grid api is ready. Catches
  // the case where the saved view includes both custom cols AND
  // columnState — applying state before custom cols are merged would
  // silently drop widths/order/sort for the unknown colIds.
  useEffect(() => {
    if (!pendingColumnStateRef.current) return;
    const api = sessionGridApiRef.current?.api;
    if (!api?.applyColumnState) return;
    api.applyColumnState({
      state: pendingColumnStateRef.current,
      applyOrder: true,
    });
    pendingColumnStateRef.current = null;
  }, [sessionColumns]);

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
        canSaveView={canSaveViewDeferred}
        onSaveView={handleSaveView}
        graphFilters={extraFilters}
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
          isOnSavedView={Boolean(activeViewConfig)}
        />
      </Box>

      {/* Column configure dropdown */}
      <ColumnConfigureDropDown
        open={openColumnConfigure}
        onClose={() => setOpenColumnConfigure(false)}
        anchorEl={columnConfigureRef?.current}
        columns={displayColumns}
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
