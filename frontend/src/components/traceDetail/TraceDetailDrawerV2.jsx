import React, { useState, useCallback, useEffect, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Drawer,
  Menu,
  MenuItem,
  Portal,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import Iconify from "src/components/iconify";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import { useSharedIssueTrace } from "src/api/shared-links";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { modelCatalogQuery } from "src/api/model/model";
import { useCreatePromptDraft } from "src/api/develop/prompt";
import { getOutputFormatFromCatalogType } from "src/sections/develop-detail/RunPrompt/common";
import logger from "src/utils/logger";
import DrawerHeader from "./DrawerHeader";
import { CaptureCoverageNotice } from "./TraceDetailPanel";
import DrawerToolbar from "./DrawerToolbar";
import TraceDisplayPanel, { DEFAULT_VIEW_CONFIG } from "./TraceDisplayPanel";
import {
  useGetSavedViews,
  useUpdateSavedView,
  useDeleteSavedView,
  useReorderSavedViews,
} from "src/api/project/saved-views";
import SpanTreeTimeline from "./SpanTreeTimeline";
import SpanDetailPane from "./SpanDetailPane";
import LeftPanelSplit from "./TraceLeftPanel";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import FalconAISidebar from "src/sections/falcon-ai/FalconAISidebar";
import {
  formatLatency,
  formatTokenCount,
  formatCost,
} from "src/sections/projects/LLMTracing/formatters";
import { enqueueSnackbar } from "notistack";
import { ShareDialog } from "src/components/share-dialog";
import AddToQueueDialog from "src/sections/annotations/queues/components/add-to-queue-dialog";
import { openAddData } from "src/sections/common/AddData/addDataStore";
import AnnotationSidebarContent from "src/components/traceDetailDrawer/AnnotationSidebarContent";
import AddLabelDrawer from "src/components/traceDetailDrawer/AddLabelDrawer";
import { buildTraceAnnotationSources } from "src/components/voiceAnnotationSources";
import AddTagsPopover from "./AddTagsPopover";
import { buildPromptConfigFromSpan } from "./promptFromSpan";
import { useNavigate } from "react-router";
import TraceFilterPanel from "src/sections/projects/LLMTracing/TraceFilterPanel";
import ImagineTab from "src/components/imagine/ImagineTab";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import useImagineStore from "src/components/imagine/useImagineStore";
import FindBar from "src/sections/common/Find/FindBar";
import ContentSkeleton from "src/components/content-skeleton";

const PANEL_WIDTH = "60vw";

const CREATE_PROMPT_ERROR = "Failed to create prompt. Please try again.";

const SPAN_FILTER_DEFAULT = {
  columnId: "",
  filterConfig: { filterType: "", filterOp: "", filterValue: "" },
};

export function TraceDetailSpanFilterPanel({
  anchorEl,
  open,
  onClose,
  currentFilters,
  onApply,
  projectId,
}) {
  return (
    <TraceFilterPanel
      anchorEl={anchorEl}
      open={open}
      onClose={onClose}
      currentFilters={currentFilters}
      onApply={onApply}
      projectId={projectId}
      source="traces"
      tab="spans"
      propertyNamespace="traces"
      attributeSource="spans"
      isSpansView
    />
  );
}

TraceDetailSpanFilterPanel.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  currentFilters: PropTypes.array.isRequired,
  onApply: PropTypes.func.isRequired,
  projectId: PropTypes.string.isRequired,
};

const ACTION_ITEMS = [
  { id: "dataset", label: "Add to dataset", icon: "solar:database-linear" },
  {
    id: "workbench",
    label: "Iterate in prompt workbench",
    icon: "solar:pen-new-square-linear",
    spanTypes: ["llm"],
  },
  // {
  //   id: "playground",
  //   label: "Iterate in agent playground",
  //   icon: "solar:gamepad-linear",
  // },
  { id: "tags", label: "Add Tags", icon: "solar:tag-linear" },
  { id: "annotate", label: "Annotate", icon: "solar:chat-round-line-linear" },
  {
    id: "queue",
    label: "Add to annotation queue",
    icon: "solar:list-plus-linear",
  },
];

function getSpan(entry) {
  return entry?.observation_span || {};
}

// Shown in place of the span tree or timeline when the trace has spans but
// the Span Type setting or the span filters hide all of them, so a filtered
// trace does not read as an empty one.
function HiddenSpansNotice({ byType, byFilters, onShowAll }) {
  const cause =
    byType && byFilters
      ? "The Span Type setting in Display and the span filters hide"
      : byType
        ? "The Span Type setting in Display hides"
        : "The span filters hide";
  return (
    <Box sx={{ p: 3, textAlign: "center" }}>
      <Typography variant="body2" fontSize={13} fontWeight={600}>
        All spans are hidden
      </Typography>
      <Typography
        variant="body2"
        color="text.secondary"
        fontSize={12}
        sx={{ mt: 0.5 }}
      >
        {`${cause} every span in this trace.`}
      </Typography>
      <Button
        size="small"
        variant="outlined"
        onClick={onShowAll}
        sx={{ mt: 1.5 }}
      >
        Show all spans
      </Button>
    </Box>
  );
}

HiddenSpansNotice.propTypes = {
  byType: PropTypes.bool,
  byFilters: PropTypes.bool,
  onShowAll: PropTypes.func.isRequired,
};

const TRACE_LAYOUTS = [
  { value: "tree", label: "Tree" },
  { value: "timeline", label: "Timeline" },
];

// Tree | Timeline in the toolbar, beside Display, so the timeline is one
// click away. The Display panel keeps its own Trace View / Timeline View
// choice; both set the same viewMode.
function TraceLayoutSwitch({ viewMode, onChange }) {
  return (
    <ToggleButtonGroup
      exclusive
      size="small"
      aria-label="Trace layout"
      // The content area draws the tree for any mode but "timeline".
      value={viewMode === "timeline" ? "timeline" : "tree"}
      // Clicking the pressed option sends null: keep one option pressed.
      onChange={(_, next) => {
        if (next) onChange(next);
      }}
      // Never squeezed or wrapped by a narrow drawer (1024 px window).
      sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
    >
      {TRACE_LAYOUTS.map((layout) => (
        <ToggleButton key={layout.value} value={layout.value}>
          {layout.label}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
}

TraceLayoutSwitch.propTypes = {
  viewMode: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};

const TraceDetailDrawerV2 = ({
  traceId,
  open,
  onClose,
  projectId,
  testExecutionId,
  onPrev,
  onNext,
  hasPrev = true,
  hasNext = true,
  initialFullscreen = false,
  initialSpanId = null,
  initialEvalFocus = false,
  refreshParentGrid,
  readOnly = false,
  sharedToken = null,
}) => {
  const navigate = useNavigate();

  const { mutate: createPromptDraft, isPending: isCreatingDraft } =
    useCreatePromptDraft({
      onSuccess: (res) => {
        const newId =
          res?.data?.result?.rootTemplate ||
          res?.data?.result?.root_template ||
          res?.data?.result?.id;
        if (newId) {
          navigate(`/dashboard/workbench/create/${newId}?tab=Playground`);
        }
      },
      onError: () => {
        enqueueSnackbar(CREATE_PROMPT_ERROR, {
          variant: "error",
        });
      },
    });

  const { mutate: createGraphFromTrace, isPending: isCreatingGraph } =
    useMutation({
      mutationFn: (body) =>
        axios.post(endpoints.agentPlayground.createGraphFromTrace, body),
      onSuccess: (res) => {
        const result = res?.data?.result;
        if (result?.graph_id || result?.graphId) {
          const graphId = result.graph_id || result.graphId;
          const versionId = result.version_id || result.versionId;
          navigate(
            `/dashboard/agents/playground/${graphId}/build?version=${versionId}`,
          );
        }
      },
      onError: () => {
        enqueueSnackbar("Failed to create agent graph from trace.", {
          variant: "error",
        });
      },
    });

  const [selectedSpanId, setSelectedSpanId] = useState(null);
  const [viewMode, setViewMode] = useState(() => {
    try {
      const raw = localStorage.getItem(`trace-view-default-${projectId}`);
      return raw ? JSON.parse(raw).viewMode || "tree" : "tree";
    } catch {
      return "tree";
    }
  });
  const [leftPanelWidth, setLeftPanelWidth] = useState(40); // percentage
  const [actionsAnchorEl, setActionsAnchorEl] = useState(null);
  const [isFullscreen, setIsFullscreen] = useState(initialFullscreen);
  const [drawerWidth, setDrawerWidth] = useState(60); // percentage of viewport
  const [shareDialogOpen, setShareDialogOpen] = useState(false);

  // When the Falcon sidebar is open (e.g. user clicked "Fix with Falcon"),
  // the trace drawer slides left by SIDEBAR_WIDTH so the sidebar is visible
  // on the right instead of being hidden behind this drawer.
  const isFalconOpen = useFalconStore((s) => s.isSidebarOpen);
  const falconOffsetPx = isFalconOpen ? FalconAISidebar.SIDEBAR_WIDTH : 0;

  // Action dialog/drawer state
  const [queueAnchorEl, setQueueAnchorEl] = useState(null);
  const [annotateDrawerOpen, setAnnotateDrawerOpen] = useState(null);
  const [addLabelDrawerOpen, setAddLabelDrawerOpen] = useState(false);
  const [tagsAnchorEl, setTagsAnchorEl] = useState(null);

  // Display panel state — load defaults from localStorage
  const storageKey = `trace-view-default-${projectId}`;
  const savedDefault = useMemo(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }, [storageKey]);

  // The Trace tab's latest display and filters in this drawer, kept by the
  // auto-save below. Imagine tabs change the live state (a saved one clears
  // the span filters), so returning to Trace restores from here.
  const traceViewRef = React.useRef(savedDefault);

  const [displayAnchorEl, setDisplayAnchorEl] = useState(null);
  const [spanTypeFilter, setSpanTypeFilter] = useState(
    savedDefault?.spanTypeFilter ?? DEFAULT_VIEW_CONFIG.spanTypeFilter,
  );
  const [visibleMetrics, setVisibleMetrics] = useState(
    savedDefault?.visibleMetrics ?? DEFAULT_VIEW_CONFIG.visibleMetrics,
  );
  const [showAgentGraph, setShowAgentGraph] = useState(
    savedDefault?.showAgentGraph ?? DEFAULT_VIEW_CONFIG.showAgentGraph,
  );

  // Span filter state — load from localStorage default if available
  const [spanFilters, setSpanFilters] = useState(
    () => savedDefault?.filters || [],
  );
  const [filterAnchorEl, setFilterAnchorEl] = useState(null);

  // ---------------------------------------------------------------------------
  // Saved Views — auto-save per tab
  // ---------------------------------------------------------------------------
  const { data: savedViewsData } = useGetSavedViews(
    readOnly ? null : projectId,
  );
  const { mutate: updateSavedView } = useUpdateSavedView(projectId);
  const { mutate: deleteSavedView } = useDeleteSavedView(projectId);
  const { mutate: reorderSavedViews } = useReorderSavedViews(projectId);

  // The list endpoint returns `custom_views`; memoized so it's a stable dep for
  // the callbacks below (a fresh `|| []` array each render would defeat them).
  const customViews = useMemo(
    () => savedViewsData?.custom_views ?? [],
    [savedViewsData],
  );

  const [activeDrawerTab, setActiveDrawerTab] = useState("trace");
  const [deleteTabId, setDeleteTabId] = useState(null);

  const drawerTabs = useMemo(() => {
    const tabs = [
      {
        id: "trace",
        label: "Trace",
        icon: "solar:link-linear",
        isDefault: true,
      },
    ];
    // Only show "imagine" tabs in the trace detail drawer — "traces" tabs belong to the list page
    customViews
      .filter((v) => (v.tab_type || v.tabType) === "imagine")
      .forEach((v) => {
        tabs.push({
          id: v.id,
          label: v.name,
          icon: "solar:stars-minimalistic-linear",
          isDefault: false,
          config: v.config,
          visibility: v.visibility,
          tabType: "imagine",
        });
      });
    // Include the unsaved Imagine tab so it appears in the toolbar with a close button
    if (activeDrawerTab === "__new_imagine__") {
      tabs.push({
        id: "__new_imagine__",
        label: "Imagine",
        icon: "solar:stars-minimalistic-linear",
        isDefault: false,
        tabType: "imagine",
      });
    }
    return tabs;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customViews, activeDrawerTab]);

  // Apply saved view config when switching tabs
  const handleTabChange = useCallback(
    (tabId) => {
      setActiveDrawerTab(tabId);
      if (tabId === "trace") {
        // Restore the Trace settings last chosen in this drawer, not the
        // default read when it opened (the hardcoded default if none).
        const def = traceViewRef.current || DEFAULT_VIEW_CONFIG;
        setViewMode(def.viewMode || "tree");
        setSpanTypeFilter(def.spanTypeFilter ?? null);
        setVisibleMetrics(
          def.visibleMetrics || DEFAULT_VIEW_CONFIG.visibleMetrics,
        );
        setShowAgentGraph(def.showAgentGraph ?? true);
        setSpanFilters(def.filters || []);
        return;
      }
      const tab = drawerTabs.find((t) => t.id === tabId);
      if (tab?.config) {
        const display = tab.config.display || tab.config;
        if (display.viewMode) setViewMode(display.viewMode);
        if (display.spanTypeFilter !== undefined)
          setSpanTypeFilter(display.spanTypeFilter);
        if (display.visibleMetrics) setVisibleMetrics(display.visibleMetrics);
        if (display.showAgentGraph !== undefined)
          setShowAgentGraph(display.showAgentGraph);
        setSpanFilters(tab.config.filters || []);
      }
    },
    [drawerTabs],
  );

  // Auto-save display settings when they change (debounced)
  const autoSaveTimerRef = React.useRef(null);
  useEffect(() => {
    if (activeDrawerTab === "trace") {
      // For default tab, persist to localStorage (display + filters)
      const config = {
        viewMode,
        spanTypeFilter,
        visibleMetrics,
        showAgentGraph,
        filters: spanFilters,
      };
      traceViewRef.current = config;
      localStorage.setItem(storageKey, JSON.stringify(config));
      return;
    }
    // Skip auto-save for imagine tabs (they save via their own Save View button)
    if (isImagineActive) return;
    // For custom tabs, auto-save to backend (debounced)
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => {
      const config = {
        display: { viewMode, spanTypeFilter, visibleMetrics, showAgentGraph },
        filters: spanFilters,
      };
      updateSavedView({ id: activeDrawerTab, config });
    }, 1000);
    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
  }, [
    viewMode,
    spanTypeFilter,
    visibleMetrics,
    showAgentGraph,
    spanFilters,
    activeDrawerTab,
    storageKey,
    updateSavedView,
  ]);

  // Reset view to defaults
  const handleResetView = useCallback(() => {
    setViewMode(DEFAULT_VIEW_CONFIG.viewMode);
    setSpanTypeFilter(DEFAULT_VIEW_CONFIG.spanTypeFilter);
    setVisibleMetrics(DEFAULT_VIEW_CONFIG.visibleMetrics);
    setShowAgentGraph(DEFAULT_VIEW_CONFIG.showAgentGraph);
  }, []);

  // Close (delete) a custom view tab
  const handleCloseTab = useCallback(
    (tabId) => {
      if (tabId === "trace") return;
      // Unsaved Imagine tab — no API call needed. Return through the Trace
      // restore: a saved Imagine tab opened before it cleared the span filters.
      if (tabId === "__new_imagine__") {
        handleTabChange("trace");
        return;
      }
      setDeleteTabId(tabId);
    },
    [handleTabChange],
  );

  // Create a new Imagine tab (temporary until saved)
  const handleCreateImagineTab = useCallback(() => {
    useImagineStore.getState().reset();
    setActiveDrawerTab("__new_imagine__");
  }, []);

  const queryClient = useQueryClient();
  // A shared issue's viewer may be signed out: read through the share token.
  const ownTrace = useGetTraceDetail(open && !sharedToken ? traceId : null, {
    projectId,
    testExecutionId,
  });
  const sharedTrace = useSharedIssueTrace(
    sharedToken,
    open && sharedToken ? traceId : null,
  );
  const {
    data: traceData,
    isLoading,
    isError,
    error,
    isFetching,
    refetch,
  } = sharedToken ? sharedTrace : ownTrace;
  // A failed refresh must not present cached spans or totals as current data.
  const data = isError ? undefined : traceData;
  const accessDenied = error?.statusCode === 401 || error?.statusCode === 403;
  // Over the trace view's span, eval or annotation limit: every retry fails
  // the same way, so no Retry is offered.
  const traceTooLarge = error?.code === "trace_too_large";

  const handleRefresh = useCallback(() => {
    refetch();
  }, [refetch]);

  // The timeline's own metrics button turns latency and tokens on or off
  // together, as the tree's does (TraceLeftPanel).
  const handleToggleTimelineMetrics = useCallback(() => {
    setVisibleMetrics((prev) => {
      const next = !(prev.latency || prev.tokens);
      return { ...prev, latency: next, tokens: next };
    });
  }, []);

  const rawSpans = data?.observation_spans;
  const summary = data?.summary;
  const graph = data?.graph;

  // Apply span type filter + span filters to the tree
  const spans = useMemo(() => {
    if (!rawSpans?.length) return rawSpans;

    // First apply span type filter from Display panel
    const hasTypeFilter =
      spanTypeFilter &&
      Array.isArray(spanTypeFilter) &&
      spanTypeFilter.length > 0;

    // Then apply span filters from Filter panel
    const activeFilters = (spanFilters || []).filter(
      (f) => f.field && Array.isArray(f.value) && f.value.length > 0,
    );

    if (!hasTypeFilter && activeFilters.length === 0) return rawSpans;

    // Map filter field names to span property accessors
    function getSpanValue(span, field) {
      const attrs = span.span_attributes || {};
      switch (field) {
        case "traceName":
          return span.name;
        case "status":
          return span.status;
        case "model":
          return span.model;
        case "nodeType":
          return span.observation_type;
        case "provider":
          return span.provider;
        case "service_name":
          return span.name;
        case "userId":
          return span.user_id;
        default:
          // Check span attributes for custom fields
          return attrs[field] ?? span[field];
      }
    }

    function matchesSpan(entry) {
      const span = getSpan(entry);

      // Span type filter from Display panel
      if (hasTypeFilter) {
        const type = (span.observation_type || "").toLowerCase();
        if (!spanTypeFilter.some((t) => t.toLowerCase() === type)) return false;
      }

      // Span filters from Filter panel
      if (activeFilters.length === 0) return true;
      return activeFilters.every((f) => {
        const spanVal = getSpanValue(span, f.field);
        if (spanVal === undefined || spanVal === null) return false;

        const sVal = String(spanVal).toLowerCase();
        const matchValues = f.value.map((v) => String(v).toLowerCase());

        switch (f.operator) {
          case "is":
            return matchValues.some((v) => sVal === v);
          case "is_not":
            return matchValues.every((v) => sVal !== v);
          case "contains":
            return matchValues.some((v) => sVal.includes(v));
          case "not_contains":
            return matchValues.every((v) => !sVal.includes(v));
          default:
            return matchValues.some((v) => sVal === v);
        }
      });
    }

    function filterTree(entries) {
      if (!entries) return [];
      return entries
        .map((entry) => {
          const filteredChildren = filterTree(entry.children);
          const selfMatches = matchesSpan(entry);
          // Keep span if it matches OR any child matches (preserve tree structure)
          if (selfMatches || filteredChildren.length > 0) {
            return {
              ...entry,
              children: filteredChildren,
              _filterMatch: selfMatches,
            };
          }
          return null;
        })
        .filter(Boolean);
    }

    return filterTree(rawSpans);
  }, [rawSpans, spanFilters, spanTypeFilter]);

  // The trace has spans, but the filters above hide every one. The notice
  // uses the memo's own tests to name the filters that are set.
  const spansHiddenByFilter = rawSpans?.length > 0 && !spans?.length;
  const typeFilterActive =
    Array.isArray(spanTypeFilter) && spanTypeFilter.length > 0;
  const spanFiltersActive = (spanFilters || []).some(
    (f) => f.field && Array.isArray(f.value) && f.value.length > 0,
  );

  // The Trace auto-save above stores this, so later traces in the project
  // open with every span shown.
  const handleShowAllSpans = useCallback(() => {
    setSpanTypeFilter(null);
    setSpanFilters([]);
  }, []);

  const hiddenSpansNotice = spansHiddenByFilter ? (
    <HiddenSpansNotice
      byType={typeFilterActive}
      byFilters={spanFiltersActive}
      onShowAll={handleShowAllSpans}
    />
  ) : null;

  // Auto-select root span when trace data loads (or when trace changes).
  // If caller passed ``initialSpanId`` (e.g. opened from span-grid), prefer
  // that so the drawer lands on the clicked span.
  const rootSpanId =
    rawSpans?.find((entry) => !getSpan(entry).parent_span_id)?.observation_span
      ?.id ?? null;
  useEffect(() => {
    setSelectedSpanId(initialSpanId || rootSpanId || null);
  }, [traceId, rootSpanId, initialSpanId]);

  // Find selected span in tree. A span the Display span-type filter or the
  // span filters hide (e.g. one clicked in the Spans grid) is still looked up
  // in the unfiltered spans, so its details open instead of an empty pane.
  const selectedSpanData = useMemo(() => {
    if (!selectedSpanId) return null;
    function find(entries) {
      for (const entry of entries || []) {
        const span = getSpan(entry);
        if (span?.id === selectedSpanId) return entry;
        if (entry.children?.length) {
          const found = find(entry.children);
          if (found) return found;
        }
      }
      return null;
    }
    return find(spans) || find(rawSpans);
  }, [selectedSpanId, spans, rawSpans]);

  const handleSelectSpan = useCallback((spanId) => {
    setSelectedSpanId((prev) => (prev === spanId ? null : spanId));
  }, []);

  // Evaluation-rollup "View span" is navigation, not a tree toggle. In
  // particular, viewing the already-selected root must keep the detail pane
  // visible instead of clearing the selection.
  const handleViewSpan = useCallback((spanId) => {
    setSelectedSpanId(spanId);
  }, []);

  const handleAction = useCallback(
    (actionId, anchorEl) => {
      if (actionId === "_open") {
        setActionsAnchorEl(anchorEl);
        return;
      }
      setActionsAnchorEl(null);

      const span = selectedSpanData ? getSpan(selectedSpanData) : null;

      switch (actionId) {
        case "queue":
          setQueueAnchorEl(actionsAnchorEl);
          break;
        case "annotate":
          setAnnotateDrawerOpen({
            spanId: span?.id || null,
            spanName: span?.name || null,
            observationType: span?.observation_type || null,
          });
          break;
        case "dataset":
          // R2 (P5): one way in; the global Add-data sheet.
          if (traceId)
            openAddData({
              source: {
                kind: "traces",
                trace_ids: [traceId],
                project_id: projectId,
              },
            });
          break;
        case "workbench": {
          if (!span) {
            enqueueSnackbar("Select a span first", { variant: "warning" });
            break;
          }
          const promptTemplateId =
            span.prompt_template_id || span.promptTemplateId;
          if (promptTemplateId) {
            navigate(
              `/dashboard/workbench/create/${promptTemplateId}?tab=Playground`,
            );
            break;
          }
          const {
            messages,
            variableNames,
            model,
            provider,
            parameters,
            responseFormat,
          } = buildPromptConfigFromSpan(span);
          (async () => {
            try {
              // Resolve the trace model to one catalog entry; set it only when sure
              // (exact or unique provider-scoped match), else leave it unset so the
              // "Select a model" flow handles it instead of a half-recognized model.
              let resolved = null;
              if (model) {
                try {
                  const data =
                    (await queryClient.fetchQuery(modelCatalogQuery(model))) ||
                    {};
                  const results = data.results || [];
                  const nameOf = (m) => m.model_name ?? "";
                  const provOf = (m) => (m.providers ?? "").toLowerCase();
                  // Catalog ids are LiteLLM-prefixed (vertex_ai/…, bedrock/<region>/…);
                  // the trace emits the provider-native id.
                  const baseName = (n) => n.slice(n.lastIndexOf("/") + 1);
                  const wantProv = provider?.toLowerCase();

                  // Exact model_name, preferring the trace's provider.
                  let match =
                    (wantProv &&
                      results.find(
                        (m) => nameOf(m) === model && provOf(m) === wantProv,
                      )) ||
                    results.find((m) => nameOf(m) === model);

                  // Else a unique prefix-stripped match within the provider;
                  // ambiguous (e.g. multi-region Bedrock) stays unresolved.
                  if (!match && !data.next) {
                    let cands = results.filter(
                      (m) => baseName(nameOf(m)) === model,
                    );
                    if (wantProv) {
                      const byProv = cands.filter(
                        (m) => provOf(m) === wantProv,
                      );
                      if (byProv.length) cands = byProv;
                    }
                    if (cands.length === 1) [match] = cands;
                  }

                  if (match) {
                    resolved = {
                      model_name: nameOf(match),
                      providers: match.providers ?? provider ?? "",
                      logoUrl: match.logo_url ?? "",
                      type: match.type ?? "",
                      isAvailable: match.is_available ?? false,
                    };
                  }
                } catch (err) {
                  logger.warn("workbench model catalog lookup failed", err);
                }
              }
              const configuration = {
                ...parameters,
                output_format: getOutputFormatFromCatalogType(resolved?.type),
                // Set explicitly — the selector defaults to JSON when unset.
                response_format: responseFormat,
                ...(resolved && {
                  model: resolved.model_name,
                  model_detail: resolved,
                }),
              };
              createPromptDraft({ configuration, messages, variableNames });
            } catch {
              enqueueSnackbar(CREATE_PROMPT_ERROR, {
                variant: "error",
              });
            }
          })();
          break;
        }
        case "playground":
          if (traceId) {
            createGraphFromTrace({ trace_id: traceId });
          }
          break;
        case "tags":
          setTagsAnchorEl(anchorEl || document.body);
          break;
        default:
          // options-truth L3 G10 (S1): every listed action has a case above;
          // an unknown id is a wiring bug, never a `coming soon` toast.
          logger.warn(`trace drawer: unknown action ${actionId}`);
      }
    },
    [
      selectedSpanData,
      projectId,
      navigate,
      createPromptDraft,
      createGraphFromTrace,
      traceId,
      actionsAnchorEl,
      queryClient,
    ],
  );

  // Resizable divider drag handler
  const handleDragStart = useCallback(
    (e) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = leftPanelWidth;
      const drawer = e.target.closest("[data-drawer-content]");
      if (!drawer) return;
      const drawerWidth = drawer.offsetWidth;

      const onMouseMove = (moveEvent) => {
        const diff = moveEvent.clientX - startX;
        const newPct = startWidth + (diff / drawerWidth) * 100;
        setLeftPanelWidth(Math.min(70, Math.max(20, newPct)));
      };
      const onMouseUp = () => {
        document.removeEventListener("mousemove", onMouseMove);
        document.removeEventListener("mouseup", onMouseUp);
      };
      document.addEventListener("mousemove", onMouseMove);
      document.addEventListener("mouseup", onMouseUp);
    },
    [leftPanelWidth],
  );

  const isImagineActive =
    activeDrawerTab === "__new_imagine__" ||
    drawerTabs.find((t) => t.id === activeDrawerTab)?.tabType === "imagine";

  const covered = useDrawerCovered(open);
  const annotateCovered = useDrawerCovered(Boolean(annotateDrawerOpen), {
    overlay: true,
  });

  return (
    // A persistent drawer renders in place, not in a portal. Opened from
    // inside another drawer (a session's View Trace), it would sit inside that
    // drawer's panel and be hidden with it when the drawer stack covers it.
    <Portal>
      <Drawer
        anchor="right"
        open={open}
        onClose={onClose}
        variant="persistent"
        sx={coveredDrawerSx(covered)}
        PaperProps={{
          sx: {
            width: isFullscreen
              ? `calc(100vw - ${falconOffsetPx}px)`
              : `${drawerWidth}vw`,
            height: "100vh",
            position: "fixed",
            right: `${falconOffsetPx}px`,
            borderRadius: 0,
            bgcolor: "background.paper",
            display: "flex",
            flexDirection: "column",
            borderLeft: isFullscreen ? "none" : "1px solid",
            borderColor: "divider",
            transition: "none",
          },
        }}
        ModalProps={{
          BackdropProps: { style: { backgroundColor: "transparent" } },
        }}
      >
        {/* Resize handle — left edge */}
        {!isFullscreen && (
          <Box
            onMouseDown={(e) => {
              e.preventDefault();
              const startX = e.clientX;
              const startWidth = drawerWidth;
              const onMove = (moveE) => {
                const diff = startX - moveE.clientX;
                const newWidth = startWidth + (diff / window.innerWidth) * 100;
                setDrawerWidth(Math.min(95, Math.max(30, newWidth)));
              };
              const onUp = () => {
                document.removeEventListener("mousemove", onMove);
                document.removeEventListener("mouseup", onUp);
                document.body.style.cursor = "";
                document.body.style.userSelect = "";
              };
              document.body.style.cursor = "col-resize";
              document.body.style.userSelect = "none";
              document.addEventListener("mousemove", onMove);
              document.addEventListener("mouseup", onUp);
            }}
            sx={{
              position: "absolute",
              left: 0,
              top: 0,
              bottom: 0,
              width: 4,
              cursor: "col-resize",
              zIndex: 10,
              "&:hover": { bgcolor: "primary.main", opacity: 0.3 },
            }}
          />
        )}

        <FindBar scope=".MuiDrawer-paper" label="Find in this trace" />
        {/* Header */}
        <DrawerHeader
          open={open}
          closeOnEscape={!initialFullscreen}
          traceId={traceId}
          projectId={projectId}
          onClose={onClose}
          onPrev={onPrev}
          onNext={onNext}
          hasPrev={hasPrev}
          hasNext={hasNext}
          onFullscreen={
            initialFullscreen
              ? undefined
              : () => setIsFullscreen((prev) => !prev)
          }
          isFullscreen={isFullscreen}
          onOpenNewTab={
            readOnly || initialFullscreen
              ? undefined
              : () => {
                  if (traceId && projectId) {
                    // Carry the selected span so the new tab opens on it; the
                    // full page selects the root span when none is given.
                    const spanQuery =
                      selectedSpanId && selectedSpanId !== rootSpanId
                        ? `?span_id=${encodeURIComponent(selectedSpanId)}`
                        : "";
                    window.open(
                      `/dashboard/observe/${projectId}/trace/${traceId}${spanQuery}`,
                      "_blank",
                    );
                  }
                }
          }
          onDownload={() => {
            // Download raw trace data as JSON
            if (data) {
              const blob = new Blob([JSON.stringify(data, null, 2)], {
                type: "application/json",
              });
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url;
              a.download = `trace-${traceId || "unknown"}.json`;
              a.click();
              URL.revokeObjectURL(url);
              enqueueSnackbar("Trace downloaded", { variant: "success" });
            }
          }}
          onShare={readOnly ? undefined : () => setShareDialogOpen(true)}
        />

        {/* Toolbar — tab bar + filter/display/save buttons */}
        <DrawerToolbar
          tabs={drawerTabs}
          activeTabId={activeDrawerTab}
          readOnly={readOnly}
          onTabChange={handleTabChange}
          onCloseTab={handleCloseTab}
          onCreateImagineTab={handleCreateImagineTab}
          onReorderTabs={(orderedIds) => {
            // orderedIds are the new order for imagine tabs only. Merge with the
            // other saved views (non-imagine) so their positions are preserved.
            const imagineIdSet = new Set(orderedIds);
            const imaginePosById = Object.fromEntries(
              orderedIds.map((id, i) => [id, i]),
            );
            const allViews = customViews;
            const nonImagine = allViews.filter((v) => !imagineIdSet.has(v.id));
            const merged = [
              ...nonImagine.map((v, i) => ({ id: v.id, position: i })),
              ...orderedIds.map((id) => ({
                id,
                position: nonImagine.length + imaginePosById[id],
              })),
            ];
            reorderSavedViews({ project_id: projectId, order: merged });
          }}
          onFilterOpen={(e) => setFilterAnchorEl(e?.currentTarget || e)}
          onDisplayOpen={(el) => setDisplayAnchorEl(el)}
          hasActiveFilter={spanFilters.length > 0}
          hideFilter={readOnly}
          rightSlot={
            <TraceLayoutSwitch viewMode={viewMode} onChange={setViewMode} />
          }
        />

        {/* Display options popover — hidden on Imagine tab. The drawer's
            display is auto-saved in this browser above and there is no
            project-wide drawer default, so "Set default for everyone" gets
            no handler: the panel shows it disabled with a reason (hidden
            when read-only). Saved views of tab_type "traces" belong to the
            Observe list and the drawer never reads them back. */}
        {!isImagineActive && (
          <TraceDisplayPanel
            anchorEl={displayAnchorEl}
            open={Boolean(displayAnchorEl)}
            onClose={() => setDisplayAnchorEl(null)}
            viewMode={viewMode}
            onViewModeChange={setViewMode}
            spanTypeFilter={spanTypeFilter}
            onSpanTypeFilterChange={setSpanTypeFilter}
            visibleMetrics={visibleMetrics}
            onToggleMetric={(metric) =>
              setVisibleMetrics((prev) => ({
                ...prev,
                [metric]: !prev[metric],
              }))
            }
            showAgentGraph={showAgentGraph}
            onToggleAgentGraph={() => setShowAgentGraph((prev) => !prev)}
            onResetView={handleResetView}
            hideSetDefault={readOnly}
          />
        )}

        {/* Span filter popover — hidden on Imagine tab */}
        {!isImagineActive && (
          <TraceDetailSpanFilterPanel
            anchorEl={filterAnchorEl}
            open={Boolean(filterAnchorEl)}
            onClose={() => setFilterAnchorEl(null)}
            currentFilters={spanFilters}
            onApply={(filters) => setSpanFilters(filters || [])}
            projectId={projectId}
          />
        )}

        {/* Active filter chips row — hidden on Imagine tab */}
        {!isImagineActive && spanFilters.length > 0 && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              px: 1.5,
              py: 0.5,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
              bgcolor: "background.default",
              gap: 0.5,
              minHeight: 32,
            }}
          >
            {/* Filter chips */}
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                flex: 1,
                flexWrap: "wrap",
                overflow: "hidden",
              }}
            >
              {spanFilters.map((f, idx) => (
                <Box
                  key={idx}
                  sx={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "4px",
                    px: 0.75,
                    py: 0.25,
                    bgcolor: "background.paper",
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "4px",
                    fontSize: 12,
                    color: "text.primary",
                    whiteSpace: "nowrap",
                  }}
                >
                  <Iconify
                    icon="solar:filter-linear"
                    width={12}
                    color="text.disabled"
                  />
                  <span style={{ fontWeight: 500 }}>{f.field}</span>
                  <span style={{ color: "var(--text-secondary)" }}>
                    {f.operator || "is"}
                  </span>
                  <span style={{ fontWeight: 600 }}>
                    {Array.isArray(f.value) ? f.value.join(", ") : f.value}
                  </span>
                  <Iconify
                    icon="solar:close-linear"
                    width={12}
                    sx={{
                      cursor: "pointer",
                      color: "text.disabled",
                      "&:hover": { color: "text.primary" },
                    }}
                    onClick={() => {
                      setSpanFilters((prev) => {
                        const next = prev.filter((_, i) => i !== idx);
                        return next;
                      });
                    }}
                  />
                </Box>
              ))}

              {/* + button to add more filters */}
              <Box
                onClick={(e) => setFilterAnchorEl(e.currentTarget)}
                sx={{
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 22,
                  height: 22,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "4px",
                  cursor: "pointer",
                  color: "text.disabled",
                  "&:hover": {
                    bgcolor: "action.hover",
                    color: "text.secondary",
                  },
                }}
              >
                <Iconify icon="solar:add-linear" width={14} />
              </Box>
            </Box>

            {/* Clear + Save */}
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                flexShrink: 0,
                ml: 1,
              }}
            >
              <Typography
                onClick={() => setSpanFilters([])}
                sx={{
                  fontSize: 12,
                  color: "text.secondary",
                  cursor: "pointer",
                  fontWeight: 500,
                  "&:hover": { color: "text.primary" },
                }}
              >
                Clear
              </Typography>
              <Typography
                onClick={() =>
                  enqueueSnackbar("View saved", { variant: "success" })
                }
                sx={{
                  fontSize: 12,
                  color: "text.secondary",
                  cursor: "pointer",
                  fontWeight: 500,
                  "&:hover": { color: "text.primary" },
                }}
              >
                Save
              </Typography>
            </Box>
          </Box>
        )}

        {!isError && !isLoading && (
          <CaptureCoverageNotice coverage={data?.capture_coverage} />
        )}

        {/* Main content area */}
        <Box
          data-drawer-content
          sx={{
            flex: 1,
            display: "flex",
            flexDirection: isImagineActive
              ? "column"
              : viewMode === "timeline"
                ? "column"
                : "row",
            overflow: "hidden",
          }}
        >
          {isError ? (
            <Box sx={{ p: 3, width: "100%" }}>
              <Alert
                severity="error"
                action={
                  !accessDenied &&
                  !traceTooLarge && (
                    <Button
                      color="inherit"
                      size="small"
                      onClick={handleRefresh}
                      disabled={isFetching}
                    >
                      {isFetching ? "Retrying…" : "Retry"}
                    </Button>
                  )
                }
              >
                <AlertTitle>Unable to load trace</AlertTitle>
                {traceTooLarge
                  ? "This trace is too large to open here. The trace view opens traces with up to 1,000 spans, 5,000 eval results and 5,000 annotations, and trying again will not change this."
                  : error?.statusCode === 401
                    ? "Sign in again to view this trace."
                    : error?.statusCode === 403
                      ? "You do not have permission to view this trace. Ask your workspace administrator for access."
                      : error?.statusCode === 404
                        ? "This trace is no longer available or cannot be accessed from this view. Return to the trace list and select another trace."
                        : error?.statusCode === 503
                          ? "The trace service is unavailable. Try again. If this continues, ask your administrator to check service availability and trace size limits."
                          : "The trace could not be loaded. Try again, or contact your administrator if the problem continues."}
              </Alert>
            </Box>
          ) : isImagineActive ? (
            <ImagineTab
              traceId={traceId}
              projectId={projectId}
              traceData={{ spans: rawSpans, summary, graph }}
              readOnly={activeDrawerTab !== "__new_imagine__"}
              savedViewId={
                activeDrawerTab !== "__new_imagine__" ? activeDrawerTab : null
              }
              savedWidgets={
                drawerTabs.find((t) => t.id === activeDrawerTab)?.config
                  ?.widgets
              }
              savedConversationId={
                drawerTabs.find((t) => t.id === activeDrawerTab)?.config
                  ?.conversation_id ||
                drawerTabs.find((t) => t.id === activeDrawerTab)?.config
                  ?.conversationId
              }
              onSaved={() =>
                queryClient.invalidateQueries({
                  queryKey: ["saved-views", projectId],
                })
              }
            />
          ) : isLoading ? (
            <Box
              sx={{
                flex: 1,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <ContentSkeleton variant="panel" />
            </Box>
          ) : viewMode === "timeline" ? (
            /* ── Timeline layout: full-width timeline on top, detail panel on bottom ── */
            <>
              {/* Top: Timeline (full width) */}
              <Box
                sx={{
                  flex: selectedSpanData ? "0 0 55%" : 1,
                  overflow: "auto",
                  borderBottom: selectedSpanData ? "1px solid" : "none",
                  borderColor: "divider",
                }}
              >
                {hiddenSpansNotice || (
                  <SpanTreeTimeline
                    spans={spans}
                    selectedSpanId={selectedSpanId}
                    onSelectSpan={handleSelectSpan}
                    showMetrics={Boolean(
                      visibleMetrics?.latency ||
                        visibleMetrics?.tokens ||
                        visibleMetrics?.cost,
                    )}
                    visibleMetrics={visibleMetrics}
                    onToggleMetrics={handleToggleTimelineMetrics}
                  />
                )}
              </Box>

              {/* Bottom: Span detail (appears when span clicked) */}
              {selectedSpanData && (
                <Box
                  sx={{
                    flex: 1,
                    overflow: "auto",
                    display: "flex",
                    flexDirection: "column",
                    minHeight: 0,
                  }}
                >
                  <SpanDetailPane
                    entry={selectedSpanData}
                    allSpans={spans}
                    traceStartTime={
                      spans?.length ? getSpan(spans[0])?.start_time : null
                    }
                    isRootSpan={selectedSpanId === rootSpanId}
                    traceTags={data?.trace?.tags || []}
                    projectId={projectId}
                    onClose={() => setSelectedSpanId(null)}
                    onAction={readOnly ? undefined : handleAction}
                    readOnly={readOnly}
                    onSelectSpan={handleViewSpan}
                    initialEvalFocus={initialEvalFocus}
                    drawerOpen={open}
                  />
                </Box>
              )}
            </>
          ) : (
            /* ── Tree layout: left/right split ── */
            <>
              {/* Left Panel: Tree + Agent Graph with resizable split. With
                  every span hidden it has no tree or graph to show, so the
                  notice takes its place at the same width. */}
              {hiddenSpansNotice ? (
                <Box
                  sx={{
                    width: `${leftPanelWidth}%`,
                    flexShrink: 0,
                    overflow: "auto",
                    borderRight: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  {hiddenSpansNotice}
                </Box>
              ) : (
                <LeftPanelSplit
                  leftPanelWidth={leftPanelWidth}
                  viewMode={viewMode}
                  spans={spans}
                  selectedSpanId={selectedSpanId}
                  onSelectSpan={handleSelectSpan}
                  visibleMetrics={visibleMetrics}
                  setVisibleMetrics={setVisibleMetrics}
                  showAgentGraph={showAgentGraph}
                  onRefresh={handleRefresh}
                />
              )}

              {/* Resizable divider with visual handle */}
              <Box
                onMouseDown={handleDragStart}
                sx={{
                  width: 8,
                  cursor: "col-resize",
                  bgcolor: "transparent",
                  flexShrink: 0,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  "&:hover": { "& .divider-dots": { opacity: 1 } },
                  "&:active": { "& .divider-dots": { opacity: 1 } },
                  transition: "background-color 150ms",
                }}
              >
                <Box
                  className="divider-dots"
                  sx={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "3px",
                    opacity: 0.4,
                    transition: "opacity 150ms",
                  }}
                >
                  {[0, 1, 2, 3, 4, 5].map((i) => (
                    <Box
                      key={i}
                      sx={{
                        width: 3,
                        height: 3,
                        borderRadius: "50%",
                        bgcolor: "text.disabled",
                      }}
                    />
                  ))}
                </Box>
              </Box>

              {/* Right Panel: Span detail */}
              <Box
                sx={{
                  flex: 1,
                  overflow: "auto",
                  display: "flex",
                  flexDirection: "column",
                }}
              >
                {selectedSpanData ? (
                  <SpanDetailPane
                    entry={selectedSpanData}
                    allSpans={spans}
                    traceStartTime={
                      spans?.length ? getSpan(spans[0])?.start_time : null
                    }
                    isRootSpan={selectedSpanId === rootSpanId}
                    traceTags={data?.trace?.tags || []}
                    projectId={projectId}
                    onClose={() => setSelectedSpanId(null)}
                    onAction={readOnly ? undefined : handleAction}
                    readOnly={readOnly}
                    onSelectSpan={handleViewSpan}
                    initialEvalFocus={initialEvalFocus}
                    drawerOpen={open}
                  />
                ) : (
                  /* Summary when no span selected */
                  <Box
                    sx={{
                      p: 3,
                      textAlign: "center",
                      color: "text.secondary",
                      mt: 8,
                    }}
                  >
                    <Iconify
                      icon="solar:cursor-linear"
                      width={40}
                      sx={{ mb: 1, opacity: 0.5 }}
                    />
                    <Typography variant="body2" fontSize={13}>
                      Select a span to view details
                    </Typography>
                    {summary && (
                      <Box
                        sx={{
                          mt: 2,
                          display: "flex",
                          justifyContent: "center",
                          gap: 3,
                        }}
                      >
                        <Typography variant="caption">
                          {summary.totalSpans || summary.total_spans} spans
                        </Typography>
                        <Typography variant="caption">
                          {formatLatency(
                            summary.totalDurationMs ||
                              summary.total_duration_ms,
                          )}
                        </Typography>
                        <Typography variant="caption">
                          {formatTokenCount(summary.total_tokens)}{" "}
                          {data?.capture_coverage
                            ? "observed tokens"
                            : "tokens"}
                        </Typography>
                        <Typography variant="caption">
                          {data?.capture_coverage && "Observed cost: "}
                          {formatCost(summary.total_cost)}
                        </Typography>
                      </Box>
                    )}
                  </Box>
                )}
              </Box>
            </>
          )}
        </Box>

        {/* Actions context menu */}
        <Menu
          anchorEl={actionsAnchorEl}
          open={Boolean(actionsAnchorEl)}
          onClose={() => setActionsAnchorEl(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{
            paper: {
              sx: {
                minWidth: 240,
                borderRadius: "8px",
                "& .MuiMenuItem-root": { fontSize: 13, gap: 1, py: 0.75 },
              },
            },
          }}
        >
          {ACTION_ITEMS.filter((item) => {
            if (!item.spanTypes) return true;
            const span = selectedSpanData ? getSpan(selectedSpanData) : null;
            const type = (span?.observation_type || "").toLowerCase();
            return item.spanTypes.includes(type);
          }).map((item) => (
            <MenuItem
              key={item.id}
              onClick={(e) => handleAction(item.id, e.currentTarget)}
              disabled={
                (item.id === "workbench" && isCreatingDraft) ||
                (item.id === "playground" && isCreatingGraph)
              }
            >
              <Iconify icon={item.icon} width={16} />
              {item.id === "workbench" && isCreatingDraft
                ? "Creating prompt..."
                : item.id === "playground" && isCreatingGraph
                  ? "Creating agent graph..."
                  : item.label}
            </MenuItem>
          ))}
        </Menu>

        {/* Share Dialog */}
        {traceId && (
          <ShareDialog
            open={shareDialogOpen}
            onClose={() => setShareDialogOpen(false)}
            resourceType="trace"
            resourceId={traceId}
          />
        )}

        {/* Add to Annotation Queue Dialog */}
        <AddToQueueDialog
          anchorEl={queueAnchorEl}
          onClose={() => setQueueAnchorEl(null)}
          sourceType={selectedSpanId ? "observation_span" : "trace"}
          sourceIds={
            selectedSpanId ? [selectedSpanId] : traceId ? [traceId] : []
          }
          itemName={
            selectedSpanData ? getSpan(selectedSpanData)?.name : "Trace"
          }
        />

        {/* Annotate Drawer — new sidebar UI */}
        <Drawer
          anchor="right"
          open={Boolean(annotateDrawerOpen)}
          onClose={() => setAnnotateDrawerOpen(null)}
          variant="temporary"
          sx={coveredDrawerSx(annotateCovered)}
          PaperProps={{
            sx: {
              width: 480,
              height: "100vh",
              borderRadius: 0,
              bgcolor: "background.paper",
            },
          }}
          ModalProps={{
            BackdropProps: { style: { backgroundColor: "rgba(0,0,0,0.1)" } },
          }}
        >
          <AnnotationSidebarContent
            sources={buildTraceAnnotationSources({
              traceId,
              spanId: annotateDrawerOpen?.spanId || rootSpanId,
              sessionId: data?.trace?.session,
              projectId,
            })}
            onClose={() => setAnnotateDrawerOpen(null)}
            onAddLabel={() => setAddLabelDrawerOpen(true)}
            onScoresChanged={() => {
              queryClient.invalidateQueries({
                queryKey: ["trace-detail", traceId],
              });
              if (annotateDrawerOpen?.spanId) {
                queryClient.invalidateQueries({
                  queryKey: ["span-annotation", annotateDrawerOpen.spanId],
                });
              }
              queryClient.invalidateQueries({
                queryKey: ["annotation-queues", "for-source"],
              });
              // The trace grid uses AG Grid's server-side row model with its
              // own row cache — invalidating React Query keys is not enough to
              // refresh the annotation column shown for this trace's row.
              // Ask the parent grid to refresh so the new annotation surfaces
              // without a full page reload.
              refreshParentGrid?.();
            }}
            showHeader
          />
        </Drawer>

        {/* Add Label Drawer — opens from AnnotationSidebarContent "+ Add Label" */}
        <AddLabelDrawer
          open={addLabelDrawerOpen}
          onClose={() => setAddLabelDrawerOpen(false)}
          projectId={projectId}
          onLabelsChanged={() => {
            queryClient.invalidateQueries({
              queryKey: ["trace-detail", traceId],
            });
            queryClient.invalidateQueries({
              queryKey: ["annotation-queues", "for-source"],
            });
          }}
        />

        {/* Add Tags Popover */}
        {!readOnly && (
          <AddTagsPopover
            anchorEl={tagsAnchorEl}
            open={Boolean(tagsAnchorEl)}
            onClose={() => setTagsAnchorEl(null)}
            traceId={traceId}
            spanId={selectedSpanId}
            currentTags={
              selectedSpanData
                ? getSpan(selectedSpanData)?.tags || []
                : data?.trace?.tags || []
            }
          />
        )}

        {/* Delete tab confirmation dialog */}
        <ConfirmDialog
          open={Boolean(deleteTabId)}
          onClose={() => setDeleteTabId(null)}
          title="Delete view"
          content={`Are you sure you want to delete "${drawerTabs.find((t) => t.id === deleteTabId)?.label || "this view"}"? This action cannot be undone.`}
          action={
            <Button
              size="small"
              variant="contained"
              color="error"
              onClick={() => {
                const tabId = deleteTabId;
                setDeleteTabId(null);
                deleteSavedView(tabId, {
                  onSuccess: () => {
                    if (activeDrawerTab === tabId) handleTabChange("trace");
                    enqueueSnackbar("View deleted", { variant: "info" });
                  },
                  onError: () => {
                    enqueueSnackbar("Failed to delete view", {
                      variant: "error",
                    });
                  },
                });
              }}
            >
              Delete
            </Button>
          }
        />
      </Drawer>
    </Portal>
  );
};

TraceDetailDrawerV2.propTypes = {
  traceId: PropTypes.string,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  projectId: PropTypes.string,
  testExecutionId: PropTypes.string,
  onPrev: PropTypes.func,
  onNext: PropTypes.func,
  hasPrev: PropTypes.bool,
  hasNext: PropTypes.bool,
  initialFullscreen: PropTypes.bool,
  initialSpanId: PropTypes.string,
  initialEvalFocus: PropTypes.bool,
  refreshParentGrid: PropTypes.func,
  readOnly: PropTypes.bool,
  sharedToken: PropTypes.string,
};

export default React.memo(TraceDetailDrawerV2);
