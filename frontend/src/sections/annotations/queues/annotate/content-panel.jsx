import React, {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  Divider,
  Grid,
  IconButton,
  LinearProgress,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import SessionHistory from "src/sections/projects/TracesDrawer/SessionHistory";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import { canonicalKeys, formatMs } from "src/utils/utils";
import TraceDetailDrawer from "src/components/traceDetailDrawer/trace-detail-drawer";
import { SelectedNodeProvider } from "src/components/traceDetailDrawer/selectedNodeContext";
import SpanTreeTimeline from "src/components/traceDetail/SpanTreeTimeline";
import SpanDetailPane from "src/components/traceDetail/SpanDetailPane";
import LeftPanelSplit from "src/components/traceDetail/TraceLeftPanel";
import DrawerToolbar from "src/components/traceDetail/DrawerToolbar";
import TraceDisplayPanel, {
  DEFAULT_VIEW_CONFIG,
} from "src/components/traceDetail/TraceDisplayPanel";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import {
  useGetSavedViews,
  useDeleteSavedView,
} from "src/api/project/saved-views";
import ImagineTab from "src/components/imagine/ImagineTab";
import useImagineStore from "src/components/imagine/useImagineStore";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import CallStatus from "src/sections/test/CallLogs/CallStatus";
import { format, isValid } from "date-fns";
import TestDetailDrawerScenarioTable from "src/sections/test-detail/TestDetailDrawer/TestDetailDrawerScenarioTable";
import AudioPlayerCustom from "src/sections/test-detail/TestDetailDrawer/AudioPlayerCustom";
import LeftSection from "src/components/CallLogsDetailDrawer/LeftSection";
import TestDetailDrawerRightSection from "src/sections/test-detail/TestDetailDrawer/TestDetailDrawerRightSection";
import RightSection from "src/components/CallLogsDetailDrawer/RightSection";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { formatDurationSafe } from "src/components/CallLogsDrawer/CustomCallLogHeader";
import { getCsatScoreColor } from "src/components/CallLogsDrawer/common";
import SvgColor from "src/components/svg-color";
import { useVoiceCallDetail } from "src/sections/agents/helper";
import VoiceDetailDrawerV2 from "src/components/VoiceDetailDrawerV2";

const CustomJsonViewer = lazy(
  () => import("src/components/custom-json-viewer/CustomJsonViewer"),
);

const Separator = () => (
  <Typography
    typography="s2_1"
    color="text.disabled"
    fontWeight="fontWeightRegular"
    sx={{ mx: 0.5 }}
  >
    |
  </Typography>
);

const SOURCE_LABELS = {
  dataset_row: "Dataset Row",
  trace: "Trace",
  observation_span: "Span",
  prototype_run: "Prototype",
  call_execution: "Simulation",
  trace_session: "Session",
};

export default function ContentPanel({ item }) {
  if (!item) return null;

  const content = item.source_content;
  const sourceType = item.source_type;

  if (content?.deleted) {
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography color="text.secondary">
          Source item has been deleted.
        </Typography>
      </Box>
    );
  }

  // For trace / observation_span, show the full trace view inline
  // Voice traces (from simulator projects) get the voice-specific UI
  if (sourceType === "trace" || sourceType === "observation_span") {
    const traceId = content?.trace_id;
    const isVoiceProject = content?.project_source === "simulator";
    const spanId =
      sourceType === "observation_span" ? content?.span_id : undefined;

    if (traceId && sourceType === "trace" && isVoiceProject) {
      // Voice calls mount the embedded drawer which manages its own
      // scroll; skip the padding/overflow wrapper the other sources use
      // so the drawer can fill the full content panel height.
      return (
        <Box sx={{ height: "100%", minHeight: 0, display: "flex" }}>
          <VoiceCallContent traceId={traceId} />
        </Box>
      );
    }

    if (traceId) {
      return <InlineTraceView traceId={traceId} spanId={spanId} />;
    }
    // Fallback to simple view if no trace_id
  }

  // For call_execution, show the full Call Log Details view inline
  if (sourceType === "call_execution") {
    return (
      <Box sx={{ p: 3, overflow: "auto", height: "100%" }}>
        <SimulationContent hideAnnotationTab={true} content={content} />
      </Box>
    );
  }

  return (
    <Box sx={{ p: 3, overflow: "auto", height: "100%" }}>
      <Chip
        label={SOURCE_LABELS[sourceType] || sourceType}
        size="small"
        color="primary"
        variant="outlined"
        sx={{ mb: 2 }}
      />

      {sourceType === "dataset_row" && <DatasetRowContent content={content} />}
      {sourceType === "trace" && <TraceContent content={content} />}
      {sourceType === "observation_span" && <SpanContent content={content} />}
      {sourceType === "prototype_run" && <PrototypeContent content={content} />}
      {sourceType === "trace_session" && <SessionContent content={content} />}
    </Box>
  );
}

ContentPanel.propTypes = {
  item: PropTypes.object,
};

// ---------------------------------------------------------------------------
// Inline Trace View — renders the new TraceDetailDrawerV2 layout inline
// (TraceTreeV2 + SpanDetailPane) for the annotation queue workspace.
// ---------------------------------------------------------------------------
function findEntryBySpanId(entries, spanId) {
  if (!entries || !spanId) return null;
  for (const entry of entries) {
    if (entry?.observation_span?.id === spanId) return entry;
    if (entry?.children?.length) {
      const found = findEntryBySpanId(entry.children, spanId);
      if (found) return found;
    }
  }
  return null;
}

const READ_ONLY_TAB_TOOLTIP = "Open trace project to edit the view";

function InlineTraceView({ traceId, spanId }) {
  const queryClient = useQueryClient();
  const { data, isLoading } = useGetTraceDetail(traceId);
  const projectId = data?.trace?.project;

  // Saved views — includes both traces-type custom views and imagine tabs.
  const { data: savedViewsData } = useGetSavedViews(projectId);
  const { mutate: deleteSavedView } = useDeleteSavedView(projectId);
  const [deleteTabId, setDeleteTabId] = useState(null);
  const customViews = useMemo(
    () => savedViewsData?.customViews || savedViewsData?.custom_views || [],
    [savedViewsData],
  );

  // Tabs: Trace (default) + saved traces-views + saved Imagine views.
  // When the user clicks "+ Imagine" we tack on a transient __new_imagine__
  // tab that lives only until they save or close it.
  const [activeDrawerTab, setActiveDrawerTab] = useState("trace");
  const drawerTabs = useMemo(() => {
    const tabs = [
      {
        id: "trace",
        label: "Trace",
        icon: "mdi:link-variant",
        isDefault: true,
      },
    ];
    customViews.forEach((v) => {
      const type = v.tab_type || v.tabType;
      const isImagine = type === "imagine";
      tabs.push({
        id: v.id,
        label: v.name,
        icon: isImagine ? "mdi:creation" : "mdi:link-variant",
        isDefault: false,
        config: v.config,
        visibility: v.visibility,
        tabType: isImagine ? "imagine" : "traces",
      });
    });
    if (activeDrawerTab === "__new_imagine__") {
      tabs.push({
        id: "__new_imagine__",
        label: "Imagine",
        icon: "mdi:creation",
        isDefault: false,
        tabType: "imagine",
      });
    }
    return tabs;
  }, [customViews, activeDrawerTab]);

  const isImagineActive =
    activeDrawerTab === "__new_imagine__" ||
    drawerTabs.find((t) => t.id === activeDrawerTab)?.tabType === "imagine";

  const handleCreateImagineTab = useCallback(() => {
    useImagineStore.getState().reset();
    setActiveDrawerTab("__new_imagine__");
  }, []);

  const handleCloseTab = useCallback((tabId) => {
    if (tabId === "trace") return;
    if (tabId === "__new_imagine__") {
      setActiveDrawerTab("trace");
      return;
    }
    setDeleteTabId(tabId);
  }, []);
  const [viewMode, setViewMode] = useState(DEFAULT_VIEW_CONFIG.viewMode);
  const [spanTypeFilter, setSpanTypeFilter] = useState(
    DEFAULT_VIEW_CONFIG.spanTypeFilter,
  );
  const [visibleMetrics, setVisibleMetrics] = useState(
    DEFAULT_VIEW_CONFIG.visibleMetrics,
  );
  const [showAgentGraph, setShowAgentGraph] = useState(
    DEFAULT_VIEW_CONFIG.showAgentGraph,
  );
  const [displayAnchorEl, setDisplayAnchorEl] = useState(null);

  // Apply config from a saved view tab (or reset on default).
  const handleTabChange = useCallback(
    (tabId) => {
      setActiveDrawerTab(tabId);
      if (tabId === "trace") {
        setViewMode(DEFAULT_VIEW_CONFIG.viewMode);
        setSpanTypeFilter(DEFAULT_VIEW_CONFIG.spanTypeFilter);
        setVisibleMetrics(DEFAULT_VIEW_CONFIG.visibleMetrics);
        setShowAgentGraph(DEFAULT_VIEW_CONFIG.showAgentGraph);
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
      }
    },
    [drawerTabs],
  );

  const handleResetView = useCallback(() => {
    setViewMode(DEFAULT_VIEW_CONFIG.viewMode);
    setSpanTypeFilter(DEFAULT_VIEW_CONFIG.spanTypeFilter);
    setVisibleMetrics(DEFAULT_VIEW_CONFIG.visibleMetrics);
    setShowAgentGraph(DEFAULT_VIEW_CONFIG.showAgentGraph);
  }, []);

  // Span selection.
  const [selectedSpanId, setSelectedSpanId] = useState(spanId || null);
  useEffect(() => {
    setSelectedSpanId(spanId || null);
  }, [traceId, spanId]);

  const rawSpans = data?.observation_spans;

  // Apply span-type filter from display options.
  const spans = useMemo(() => {
    if (!rawSpans?.length) return rawSpans;
    const hasTypeFilter =
      spanTypeFilter &&
      Array.isArray(spanTypeFilter) &&
      spanTypeFilter.length > 0;
    if (!hasTypeFilter) return rawSpans;

    function filterTree(entries) {
      const out = [];
      for (const entry of entries) {
        const type = (
          entry?.observation_span?.observation_type || ""
        ).toLowerCase();
        const matches = spanTypeFilter.includes(type);
        const kids = entry.children?.length ? filterTree(entry.children) : [];
        if (matches || kids.length) {
          out.push({ ...entry, children: kids });
        }
      }
      return out;
    }
    return filterTree(rawSpans);
  }, [rawSpans, spanTypeFilter]);

  const rootSpanId = spans?.length ? spans[0]?.observation_span?.id : null;

  useEffect(() => {
    if (!selectedSpanId && rootSpanId) setSelectedSpanId(rootSpanId);
  }, [rootSpanId, selectedSpanId]);

  const handleSelectSpan = useCallback((id) => {
    setSelectedSpanId((prev) => (prev === id ? null : id));
  }, []);

  const selectedEntry = useMemo(
    () => findEntryBySpanId(spans, selectedSpanId),
    [spans, selectedSpanId],
  );

  if (isLoading) {
    return (
      <Box sx={{ p: 2 }}>
        <LinearProgress />
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        bgcolor: "background.paper",
      }}
    >
      {/* Tabs toolbar — includes the "+ Imagine" button so users can pivot
          to an Imagine view from the annotate workspace. Saved views
          themselves stay read-only (no rename/edit here). */}
      <DrawerToolbar
        tabs={drawerTabs}
        activeTabId={activeDrawerTab}
        onTabChange={handleTabChange}
        onCloseTab={handleCloseTab}
        onCreateImagineTab={handleCreateImagineTab}
        onDisplayOpen={(el) => setDisplayAnchorEl(el)}
        readOnly
        readOnlyTabTooltip={READ_ONLY_TAB_TOOLTIP}
        hideFilter
      />

      {/* Display options popover */}
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
          setVisibleMetrics((prev) => ({ ...prev, [metric]: !prev[metric] }))
        }
        showAgentGraph={showAgentGraph}
        onToggleAgentGraph={() => setShowAgentGraph((prev) => !prev)}
        onResetView={handleResetView}
        hideSetDefault
      />

      {/* Body: Imagine view OR trace tree / timeline + span detail */}
      <Box
        sx={{
          display: "flex",
          flexDirection: isImagineActive
            ? "column"
            : viewMode === "timeline"
              ? "column"
              : "row",
          flex: 1,
          minHeight: 0,
          overflow: "hidden",
        }}
      >
        {isImagineActive ? (
          <ImagineTab
            traceId={traceId}
            projectId={projectId}
            entityType="trace"
            traceData={{
              spans: rawSpans,
              summary: data?.summary,
              graph: data?.graph,
            }}
            readOnly={activeDrawerTab !== "__new_imagine__"}
            savedViewId={
              activeDrawerTab !== "__new_imagine__" ? activeDrawerTab : null
            }
            savedWidgets={
              drawerTabs.find((t) => t.id === activeDrawerTab)?.config?.widgets
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
        ) : viewMode === "timeline" ? (
          <>
            <Box
              sx={{
                flex: selectedEntry ? "0 0 55%" : 1,
                overflow: "auto",
                borderBottom: selectedEntry ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              <SpanTreeTimeline
                spans={spans}
                selectedSpanId={selectedSpanId}
                onSelectSpan={handleSelectSpan}
              />
            </Box>
            {selectedEntry && (
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
                  entry={selectedEntry}
                  allSpans={spans}
                  traceStartTime={
                    spans?.length
                      ? spans[0]?.observation_span?.start_time
                      : null
                  }
                  isRootSpan={selectedSpanId === rootSpanId}
                  traceTags={data?.trace?.tags || []}
                  onSelectSpan={handleSelectSpan}
                />
              </Box>
            )}
          </>
        ) : (
          <>
            <LeftPanelSplit
              leftPanelWidth={40}
              viewMode={viewMode}
              spans={spans}
              selectedSpanId={selectedSpanId}
              onSelectSpan={handleSelectSpan}
              visibleMetrics={visibleMetrics}
              setVisibleMetrics={setVisibleMetrics}
              showAgentGraph={showAgentGraph}
            />
            <Box
              sx={{
                flex: 1,
                overflow: "auto",
                display: "flex",
                flexDirection: "column",
                minHeight: 0,
              }}
            >
              {selectedEntry ? (
                <SpanDetailPane
                  entry={selectedEntry}
                  allSpans={spans}
                  traceStartTime={
                    spans?.length
                      ? spans[0]?.observation_span?.start_time
                      : null
                  }
                  isRootSpan={selectedSpanId === rootSpanId}
                  traceTags={data?.trace?.tags || []}
                  onSelectSpan={handleSelectSpan}
                />
              ) : (
                <Box
                  sx={{
                    p: 3,
                    textAlign: "center",
                    color: "text.secondary",
                    mt: 8,
                  }}
                >
                  <Typography variant="body2" fontSize={13}>
                    Select a span to view details
                  </Typography>
                </Box>
              )}
            </Box>
          </>
        )}
      </Box>

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
              if (!tabId) return;
              deleteSavedView(tabId, {
                onSuccess: () => {
                  if (activeDrawerTab === tabId) setActiveDrawerTab("trace");
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
    </Box>
  );
}

InlineTraceView.propTypes = {
  traceId: PropTypes.string,
  spanId: PropTypes.string,
};

// ---------------------------------------------------------------------------
// VoiceCallContent — renders the voice call inline in the annotation
// workspace using the shared VoiceDetailDrawerV2. We used to rebuild
// the call log UI by hand here, which fell behind the new drawer every
// time we shipped a feature. Mounting the drawer directly keeps the
// annotate view and the main simulate/observe drawers in lockstep.
// ---------------------------------------------------------------------------
function VoiceCallContent({ traceId }) {
  const { data: callData, isLoading } = useVoiceCallDetail(traceId, true);

  if (isLoading) {
    return (
      <Box sx={{ py: 4 }}>
        <LinearProgress />
      </Box>
    );
  }

  if (!callData) {
    return (
      <Typography color="text.secondary">
        Voice call data not available.
      </Typography>
    );
  }

  const drawerData = {
    // Annotation workspace sources voice traces from simulate — keep
    // the module as "simulate" so the drawer wires up the simulate
    // analytics / path analysis / audio paths correctly.
    module: "simulate",
    id: traceId,
    trace_id: traceId,
    // Needed by VoiceDetailDrawerV2 to fetch saved views (Imagine tabs) —
    // the annotate route has no `observeId` URL param, so the drawer falls
    // back to data.project_id.
    project_id: callData.project_id,
    status: callData.status,
    simulationCallType: "voice",
    callType: callData.call_type,
    timestamp: callData.created_at || callData.started_at,
    duration: callData.duration_seconds,
    scenario: callData.scenario_name,
    scenarioId: callData.scenario_id,
    scenarioColumns: callData.scenario_columns || {},
    customerName: callData.customer_name || callData.phone_number,
    phoneNumber: callData.phone_number,
    endedReason: callData.ended_reason,
    overallScore: callData.overall_score,
    transcript: callData.transcript || [],
    // Flat recording format — merge span-attribute URLs with raw_log fallbacks
    recording: {
      combined:
        callData.recording?.mono?.combined_url || callData.recording_url || "",
      stereo:
        callData.recording?.stereo_url || callData.stereo_recording_url || "",
      assistant: callData.recording?.mono?.assistant_url || "",
      customer: callData.recording?.mono?.customer_url || "",
    },
    recordings: callData.recording,
    recordingAvailable:
      callData.recording_available ??
      !!(
        callData.recording?.mono?.combined_url ||
        callData.recording?.stereo_url ||
        callData.recording_url
      ),
    audioUrl:
      callData.recording?.mono?.combined_url ||
      callData.recording?.stereo_url ||
      callData.recording_url,
    callSummary: callData.call_summary,
    customerLatencyMetrics: callData.customer_latency_metrics,
    customerCostBreakdown: callData.customer_cost_breakdown,
    evalMetrics: callData.eval_outputs || {},
    evalOutputs: callData.eval_outputs || {},
    overallStatus: callData.overall_status || callData.status,
     turn_count: callData.turn_count,
    talk_ratio: callData.talk_ratio,
    agent_talk_percentage: callData.agent_talk_percentage,
    avg_agent_latency_ms: callData.avg_agent_latency_ms,
    user_wpm: callData.user_wpm,
    bot_wpm: callData.bot_wpm,
    user_interruption_count: callData.user_interruption_count,
    ai_interruption_count: callData.ai_interruption_count,
    // Key must be snake_case: VoiceRightPanel reads `data.observation_span`
    // to pull the root conversation span (and its `span_attributes.raw_log` +
    // `call_logs`) that drive the Attributes and Logs tabs.
    observation_span: callData.observation_span || [],
    traceId,
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        width: "100%",
        height: "100%",
      }}
    >
      <VoiceDetailDrawerV2
        data={drawerData}
        onClose={() => {}}
        hasPrev={false}
        hideAnnotationTab={true}
        hasNext={false}
        scenarioId={drawerData.scenarioId}
        embedded
      />
    </Box>
  );
}

VoiceCallContent.propTypes = {
  traceId: PropTypes.string,
};

// ---------------------------------------------------------------------------
// Typed field renderer — picks the right display based on data type
// ---------------------------------------------------------------------------
function FieldValue({ value, dataType }) {
  if (value === null || value === undefined) return <span>—</span>;

  const type = (dataType || "").toLowerCase();

  // Image
  if (type === "image" || type === "images") {
    return <ImageValue value={value} />;
  }

  // Audio
  if (type === "audio") {
    return <AudioValue value={value} />;
  }

  // Document / file
  if (type === "document") {
    return <DocumentValue value={value} />;
  }

  // JSON / array / object
  if (type === "json" || type === "array") {
    return <JsonValue value={value} />;
  }

  // Boolean
  if (type === "boolean") {
    const bool = value === "true" || value === true;
    return (
      <Chip
        label={bool ? "True" : "False"}
        color={bool ? "success" : "default"}
        size="small"
        variant="outlined"
      />
    );
  }

  // Auto-detect from value if no explicit type
  if (
    !type ||
    type === "text" ||
    type === "others" ||
    type === "integer" ||
    type === "float" ||
    type === "datetime" ||
    type === "persona"
  ) {
    // Try to detect image URLs/base64
    if (
      typeof value === "string" &&
      (value.startsWith("data:image") ||
        /\.(png|jpg|jpeg|gif|webp|svg)(\?|$)/i.test(value))
    ) {
      return <ImageValue value={value} />;
    }
    // Try to detect audio URLs/base64
    if (
      typeof value === "string" &&
      (value.startsWith("data:audio") ||
        /\.(mp3|wav|ogg|m4a|aac|flac|webm)(\?|$)/i.test(value))
    ) {
      return <AudioValue value={value} />;
    }
    // Try to detect JSON strings
    if (typeof value === "string" && value.length > 1) {
      const trimmed = value.trim();
      if (
        (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
        (trimmed.startsWith("[") && trimmed.endsWith("]"))
      ) {
        try {
          const parsed = JSON.parse(trimmed);
          return <JsonValue value={parsed} />;
        } catch {
          // not JSON, fall through
        }
      }
    }
    // Object/array values
    if (typeof value === "object") {
      return <JsonValue value={value} />;
    }
  }

  // Default: plain text
  return <span>{String(value)}</span>;
}

FieldValue.propTypes = {
  value: PropTypes.any,
  dataType: PropTypes.string,
};

// ---------------------------------------------------------------------------
// Type-specific renderers
// ---------------------------------------------------------------------------
function ImageValue({ value }) {
  const [expanded, setExpanded] = useState(false);

  // Handle multiple images (array of URLs)
  const urls = Array.isArray(value)
    ? value
    : typeof value === "string" && value.startsWith("[")
      ? (() => {
          try {
            return JSON.parse(value);
          } catch {
            return [value];
          }
        })()
      : [value];

  return (
    <>
      <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", gap: 1 }}>
        {urls.map((url, i) => (
          <Box
            key={i}
            component="img"
            src={url}
            alt={`Image ${i + 1}`}
            onClick={() => setExpanded(url)}
            sx={{
              maxWidth: "100%",
              maxHeight: 300,
              borderRadius: 0.5,
              cursor: "pointer",
              objectFit: "contain",
              border: 1,
              borderColor: "divider",
              "&:hover": { opacity: 0.85 },
            }}
          />
        ))}
      </Stack>
      <Dialog
        open={!!expanded}
        onClose={() => setExpanded(false)}
        maxWidth="lg"
      >
        <Box sx={{ position: "relative" }}>
          <IconButton
            onClick={() => setExpanded(false)}
            sx={{
              position: "absolute",
              top: 8,
              right: 8,
              bgcolor: "background.paper",
            }}
            size="small"
          >
            <Iconify icon="mingcute:close-line" />
          </IconButton>
          <Box
            component="img"
            src={expanded}
            alt="Full size"
            sx={{ maxWidth: "90vw", maxHeight: "90vh", display: "block" }}
          />
        </Box>
      </Dialog>
    </>
  );
}

ImageValue.propTypes = {
  value: PropTypes.any,
};

function AudioValue({ value }) {
  const src = typeof value === "string" ? value : value?.url || "";
  if (!src) return <span>—</span>;

  return (
    <Box
      component="audio"
      controls
      src={src}
      sx={{ width: "100%", maxWidth: 500 }}
    >
      Your browser does not support audio.
    </Box>
  );
}

AudioValue.propTypes = {
  value: PropTypes.any,
};

function isSafeUrl(url) {
  try {
    const parsed = new URL(url, window.location.origin);
    return ["https:", "http:", "blob:"].includes(parsed.protocol);
  } catch {
    return false;
  }
}

function DocumentValue({ value }) {
  const src = typeof value === "string" ? value : value?.url || "";
  const name = value?.fileName || value?.documentName || "Document";

  if (!src) return <span>—</span>;

  // PDF — show inline (only safe URLs)
  if (
    (src.includes(".pdf") || src.startsWith("data:application/pdf")) &&
    isSafeUrl(src)
  ) {
    return (
      <Box
        component="iframe"
        src={src}
        title={name}
        sandbox="allow-same-origin"
        sx={{
          width: "100%",
          height: 500,
          border: 1,
          borderColor: "divider",
          borderRadius: 0.5,
        }}
      />
    );
  }

  // Other documents — download link
  return (
    <Stack direction="row" spacing={1} alignItems="center">
      <Iconify icon="mingcute:file-line" width={20} />
      <Typography
        component="a"
        href={src}
        target="_blank"
        rel="noopener noreferrer"
        variant="body2"
        sx={{ color: "primary.main", textDecoration: "underline" }}
      >
        {name}
      </Typography>
    </Stack>
  );
}

DocumentValue.propTypes = {
  value: PropTypes.any,
};

function JsonValue({ value }) {
  const obj =
    typeof value === "string"
      ? (() => {
          try {
            return JSON.parse(value);
          } catch {
            return value;
          }
        })()
      : value;

  if (typeof obj !== "object" || obj === null) {
    return <span>{String(obj)}</span>;
  }

  return (
    <Suspense fallback={<CircularProgress size={16} />}>
      <CustomJsonViewer object={obj} />
    </Suspense>
  );
}

JsonValue.propTypes = {
  value: PropTypes.any,
};

// ---------------------------------------------------------------------------
// ContentSection — wraps a field with title and styled box
// ---------------------------------------------------------------------------
function ContentSection({ title, children, dataType, copyValue }) {
  const [copied, setCopied] = useState(false);
  const showCopy = copyValue !== undefined && dataType !== "json";

  const handleCopy = (e) => {
    e.stopPropagation();
    const text =
      typeof copyValue === "object"
        ? JSON.stringify(copyValue, null, 2)
        : String(copyValue ?? "");
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <Box sx={{ mb: 2 }}>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
        <Typography
          variant="caption"
          color="text.secondary"
          fontWeight={600}
          sx={{ textTransform: "none" }}
        >
          {title}
        </Typography>
        {dataType && (
          <Chip
            label={dataType}
            size="small"
            variant="outlined"
            color="primary"
            sx={{ height: 18, fontSize: 10 }}
          />
        )}
        <Box sx={{ flex: 1 }} />
        {showCopy && (
          <Tooltip title={copied ? "Copied!" : "Copy"} placement="top">
            <IconButton size="small" onClick={handleCopy} sx={{ p: 0.25 }}>
              <Iconify
                icon={copied ? "eva:checkmark-fill" : "eva:copy-fill"}
                width={14}
                sx={{ color: copied ? "success.main" : "text.disabled" }}
              />
            </IconButton>
          </Tooltip>
        )}
      </Stack>
      <Box
        sx={{
          bgcolor: "background.neutral",
          borderRadius: 0.5,
          p: 2,
          fontSize: 13,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          maxHeight: 500,
          overflow: "auto",
        }}
      >
        {children}
      </Box>
    </Box>
  );
}

ContentSection.propTypes = {
  title: PropTypes.string,
  children: PropTypes.node,
  dataType: PropTypes.string,
  copyValue: PropTypes.any,
};

// ---------------------------------------------------------------------------
// Dataset Row Content
// ---------------------------------------------------------------------------
function DatasetRowContent({ content }) {
  const fields = content?.fields || {};
  const fieldTypes = content?.field_types || {};
  const datasetName = content?.dataset_name;
  const rowOrder = content?.row_order;
  // canonicalKeys strips the camelCase aliases the axios interceptor
  // adds next to any user-authored snake_case column names so a column
  // like `user_email` doesn't render twice in the annotation panel.
  const keys = canonicalKeys(fields);
  const hasAnyData = keys.length > 0;

  return (
    <Stack spacing={1}>
      {(datasetName || rowOrder != null) && (
        <Typography variant="body2" color="text.secondary">
          {datasetName && `Dataset: ${datasetName}`}
          {datasetName && rowOrder != null && " \u00b7 "}
          {rowOrder != null && `Row #${rowOrder}`}
        </Typography>
      )}
      {hasAnyData && <Divider />}
      {hasAnyData ? (
        keys.map((key) => (
          <ContentSection
            key={key}
            title={key}
            dataType={fieldTypes[key]}
            copyValue={fields[key]}
          >
            <FieldValue value={fields[key]} dataType={fieldTypes[key]} />
          </ContentSection>
        ))
      ) : (
        <Typography color="text.secondary">No row data available.</Typography>
      )}
    </Stack>
  );
}

DatasetRowContent.propTypes = {
  content: PropTypes.object,
};

// ---------------------------------------------------------------------------
// Trace Content
// ---------------------------------------------------------------------------
function TraceContent({ content }) {
  return (
    <Stack spacing={1}>
      {content.name && (
        <Typography variant="subtitle2">{content.name}</Typography>
      )}
      <Divider />
      <ContentSection title="Input" copyValue={content.input}>
        <FieldValue value={content.input} />
      </ContentSection>
      <ContentSection title="Output" copyValue={content.output}>
        <FieldValue value={content.output} />
      </ContentSection>
      {content.metadata && Object.keys(content.metadata).length > 0 && (
        <ContentSection
          title="Metadata"
          dataType="json"
          copyValue={content.metadata}
        >
          <FieldValue value={content.metadata} dataType="json" />
        </ContentSection>
      )}
    </Stack>
  );
}

TraceContent.propTypes = {
  content: PropTypes.object,
};

// ---------------------------------------------------------------------------
// Span Content
// ---------------------------------------------------------------------------
function SpanContent({ content }) {
  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={1} alignItems="center">
        {content.name && (
          <Typography variant="subtitle2">{content.name}</Typography>
        )}
        {content.observation_type && (
          <Chip
            label={content.observation_type}
            size="small"
            variant="outlined"
          />
        )}
      </Stack>
      <Divider />
      <ContentSection title="Input" copyValue={content.input}>
        <FieldValue value={content.input} />
      </ContentSection>
      <ContentSection title="Output" copyValue={content.output}>
        <FieldValue value={content.output} />
      </ContentSection>
    </Stack>
  );
}

SpanContent.propTypes = {
  content: PropTypes.object,
};

// ---------------------------------------------------------------------------
// Prototype Content
// ---------------------------------------------------------------------------
function PrototypeContent({ content }) {
  return (
    <Stack spacing={1}>
      {content.name && (
        <Typography variant="subtitle2">{content.name}</Typography>
      )}
      {content.model && (
        <Typography variant="body2" color="text.secondary">
          Model: {content.model}
        </Typography>
      )}
      <Divider />
      <ContentSection title="Prompt" copyValue={content.prompt}>
        <FieldValue value={content.prompt} />
      </ContentSection>
      <ContentSection title="Response" copyValue={content.response}>
        <FieldValue value={content.response} />
      </ContentSection>
    </Stack>
  );
}

PrototypeContent.propTypes = {
  content: PropTypes.object,
};

// ---------------------------------------------------------------------------
// Simulation Content — reuses the same components as the Call Log Details drawer
// ---------------------------------------------------------------------------
function SimulationContent({ content, hideAnnotationTab=false }) {
  const callId = content?.call_id;

  const { data: callData, isLoading } = useQuery({
    queryKey: ["call-execution-detail", callId],
    queryFn: () => axios.get(endpoints.testExecutions.callDetail(callId)),
    select: (d) => d.data,
    enabled: !!callId,
  });

  if (isLoading) {
    return (
      <Box sx={{ py: 4 }}>
        <LinearProgress />
      </Box>
    );
  }

  // Fallback to minimal view if call data fetch fails
  if (!callData) {
    return (
      <Stack spacing={1}>
        <Stack direction="row" spacing={1}>
          {content.simulation_call_type && (
            <Chip label={content.simulation_call_type} size="small" />
          )}
          {content.status && (
            <Chip label={content.status} size="small" variant="outlined" />
          )}
        </Stack>
        <Divider />
        <ContentSection title="Input">
          <FieldValue value={content.input} />
        </ContentSection>
        <ContentSection title="Output">
          <FieldValue value={content.output} />
        </ContentSection>
      </Stack>
    );
  }

  // Shape data to match the drawer component expectations
  const simulationCallType = callData.simulation_call_type;
  const drawerData = {
    module: "simulate",
    id: callId,
    status: callData.status || callData.overall_status,
    simulationCallType,
    callType: callData.call_type,
    timestamp: callData.timestamp || callData.started_at,
    duration: callData.duration || callData.duration_seconds,
    scenario: callData.scenario || callData.scenario_name,
    scenarioId: callData.scenario_id,
    scenarioColumns: callData.scenario_columns || {},
    customerName: callData.customer_name,
    phoneNumber: callData.phone_number,
    endedReason: callData.ended_reason,
    overallScore: callData.overall_score,
    transcript: callData.transcripts || callData.transcript || [],
    recordings: callData.recordings,
    audioUrl: callData.recording_url || callData.stereo_recording_url,
    agentName: callData.agent_definition_used_name,
    simulatorName: callData.simulator_agent_name,
    callSummary: callData.call_summary,
    customerLatencyMetrics: callData.customer_latency_metrics,
    customerCostBreakdown: callData.customer_cost_breakdown,
    evalMetrics: callData.eval_outputs || callData.eval_metrics || {},
    overallStatus: callData.overall_status || callData.status,
    sessionId: callData.session_id,
    serviceProviderCallId: callData.service_provider_call_id,
    customerCallId: callData.customer_call_id,
  };

  const isVoice = simulationCallType !== AGENT_TYPES.CHAT;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      {/* Header — same style as CustomCallLogHeader without nav/close buttons */}
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
          pb: 2,
        }}
      >
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          {simulationCallType === AGENT_TYPES.CHAT
            ? "Chat Log Details"
            : "Call Log Details"}
        </Typography>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 0.5,
          }}
        >
          {drawerData.scenario && (
            <>
              <Typography
                typography="s2_1"
                color="text.disabled"
                fontWeight="fontWeightRegular"
              >
                {drawerData.scenario}
              </Typography>
              <Separator />
            </>
          )}
          {drawerData.timestamp && (
            <>
              <Typography
                typography="s2_1"
                color="text.disabled"
                fontWeight="fontWeightRegular"
              >
                {(() => {
                  const ts = new Date(drawerData.timestamp);
                  return isValid(ts)
                    ? format(ts, "yyyy-MM-dd HH:mm:ss")
                    : drawerData.timestamp;
                })()}
              </Typography>
              <Separator />
            </>
          )}
          {drawerData.duration && (
            <>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                <Iconify
                  icon="material-symbols:schedule-outline"
                  width="14px"
                  height="14px"
                  color="text.disabled"
                />
                <Typography
                  typography="s2_1"
                  color="text.disabled"
                  fontWeight="fontWeightRegular"
                >
                  {formatDurationSafe(drawerData.duration)}
                </Typography>
              </Box>
              <Separator />
            </>
          )}
          {drawerData.overallScore && (
            <>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                <Typography typography="s2_1" fontWeight="fontWeightRegular">
                  CSAT Score:
                </Typography>
                <Typography
                  typography="s2_1"
                  color={getCsatScoreColor(drawerData.overallScore)}
                  fontWeight="fontWeightSemiBold"
                >
                  {`${drawerData.overallScore}/10`}
                </Typography>
              </Box>
              <Separator />
            </>
          )}
          {drawerData.callType && isVoice && (
            <>
              <Chip
                label={
                  (drawerData.callType ?? "").toLowerCase().includes("inbound")
                    ? "Inbound"
                    : "Outbound"
                }
                icon={
                  <SvgColor
                    sx={{ width: 20 }}
                    src={
                      (drawerData.callType ?? "")
                        .toLowerCase()
                        .includes("inbound")
                        ? "/assets/icons/ic_call_inbound.svg"
                        : "/assets/icons/ic_call_outbound.svg"
                    }
                  />
                }
                size="small"
                sx={{
                  typography: "s1",
                  fontWeight: "fontWeightMedium",
                  color: "blue.700",
                  bgcolor: "blue.o10",
                  borderRadius: 0.25,
                  paddingX: 1,
                  "& .MuiChip-icon": { color: "blue.700" },
                }}
              />
              <Separator />
            </>
          )}
          <CallStatus value={drawerData.status ?? ""} />
        </Box>
        {drawerData.endedReason && (
          <Typography
            variant="s2_1"
            color="text.disabled"
            fontWeight="fontWeightRegular"
          >
            {simulationCallType === AGENT_TYPES.CHAT ? "Chat" : "Call"} end
            reason : {drawerData.endedReason}
          </Typography>
        )}
      </Box>

      {/* Scenario Details */}
      <Box
        sx={{
          "& .MuiBox-root": {
            width: "100% !important",
            flexShrink: "1 !important",
          },
        }}
      >
        <TestDetailDrawerScenarioTable data={drawerData} />
      </Box>

      {/* Recording — only for voice */}
      {isVoice && (
        <Stack
          sx={{
            mx: 0,
            display: "flex",
            flexDirection: "column",
            gap: 2,
            backgroundColor: "background.neutral",
            padding: "14px",
            borderRadius: 0.5,
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography variant="m3" fontWeight="fontWeightMedium">
            Recording
          </Typography>
          <AudioPlayerCustom data={drawerData} />
        </Stack>
      )}

      {/* Two-column layout — Left: Transcript, Right: Analytics/Evaluations */}
      <Grid container spacing={2}>
        <Grid
          item
          xs={6}
          md={6}
          display="flex"
          flexDirection="column"
          gap={2}
          height="100%"
        >
          <LeftSection data={drawerData} />
        </Grid>
        <Grid item xs={6} md={6} height="100%">
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 0.5,
              alignItems: "center",
            }}
          >
            <TestDetailDrawerRightSection
              scenarioId={drawerData.scenarioId}
              openedExecutionId={callId}
              latencies={drawerData.customerLatencyMetrics?.systemMetrics}
              analysisSummary={drawerData.callSummary}
              costBreakdown={drawerData.customerCostBreakdown}
              evalOutputs={drawerData.evalMetrics}
              callStatus={drawerData.overallStatus}
              status={drawerData.status}
              simulationCallType={drawerData.simulationCallType}
              sessionId={drawerData.sessionId}
              hideAnnotationTab={hideAnnotationTab}
            />
          </Box>
        </Grid>
      </Grid>
    </Box>
  );
}

SimulationContent.propTypes = {
  content: PropTypes.object,
    hideAnnotationTab: PropTypes.bool,
};

// ---------------------------------------------------------------------------
// Session Content — fetches session traces and renders SessionHistory
// ---------------------------------------------------------------------------
function SessionContent({ content }) {
  const sessionId = content?.session_id;
  const [openTraceData, setOpenTraceData] = useState(null);

  const {
    data: tracePages,
    isLoading,
    fetchNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["annotation-session-traces", sessionId],
    queryFn: ({ pageParam }) =>
      axios.get(`${endpoints.project.traceSession}${sessionId}/`, {
        params: { page_number: pageParam, page_size: 10 },
      }),
    getNextPageParam: (page, _, pageParams) =>
      page.data?.result?.next ? pageParams + 1 : null,
    initialPageParam: 0,
    enabled: Boolean(sessionId),
  });

  const scrollRef = useScrollEnd(() => {
    if (!isFetchingNextPage && !isLoading) {
      fetchNextPage();
    }
  }, [isFetchingNextPage, isLoading]);

  const handleTraceClick = useCallback((traceId) => {
    setOpenTraceData({ traceId });
  }, []);

  const sessionMetadata = tracePages?.pages[0]?.data?.result?.session_metadata;
  const traceDetail =
    tracePages?.pages?.reduce(
      (acc, page) => [...acc, ...(page.data?.result?.response || [])],
      [],
    ) || [];

  if (!sessionId) {
    return (
      <Typography color="text.secondary">No session data available.</Typography>
    );
  }

  return (
    <Stack spacing={1.5} sx={{ height: "100%" }}>
      {/* Session metadata chips */}
      {sessionMetadata && (
        <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", gap: 0.5 }}>
          {sessionMetadata.duration != null && (
            <Chip
              size="small"
              icon={<Iconify icon="radix-icons:clock" width={14} />}
              label={`Duration: ${formatMs(sessionMetadata.duration * 1000)}`}
              variant="outlined"
            />
          )}
          {sessionMetadata.total_cost != null && (
            <Chip
              size="small"
              label={`Cost: ${sessionMetadata.total_cost}`}
              variant="outlined"
            />
          )}
          {sessionMetadata.total_traces != null && (
            <Chip
              size="small"
              label={`Traces: ${sessionMetadata.total_traces}`}
              variant="outlined"
            />
          )}
        </Stack>
      )}

      <Divider />

      {/* Session conversation history — same UI as TracesDrawer */}
      <Box ref={scrollRef} sx={{ flex: 1, overflow: "auto" }}>
        <SessionHistory
          traceDetail={traceDetail}
          loading={isLoading}
          isFetchingNextPage={isFetchingNextPage}
          activeSessionId={sessionId}
          onTraceClick={handleTraceClick}
        />
      </Box>

      {/* Trace detail side drawer */}
      <SelectedNodeProvider>
        <TraceDetailDrawer
          open={Boolean(openTraceData)}
          onClose={() => setOpenTraceData(null)}
          traceData={openTraceData}
          setTraceDetailDrawerOpen={null}
          setSelectedTraceId={(newTraceId) =>
            setOpenTraceData((prev) => ({ ...prev, traceId: newTraceId }))
          }
          viewOptions={{ showAnnotation: true, showNavigation: false }}
        />
      </SelectedNodeProvider>
    </Stack>
  );
}

SessionContent.propTypes = {
  content: PropTypes.object,
};
