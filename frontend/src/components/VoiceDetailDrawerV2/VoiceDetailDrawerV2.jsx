import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, CircularProgress, Stack } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { useParams } from "react-router";

import DrawerToolbar from "src/components/traceDetail/DrawerToolbar";
import ImagineTab from "src/components/imagine/ImagineTab";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import { ShareDialog } from "src/components/share-dialog";
import AddTagsPopover from "src/components/traceDetail/AddTagsPopover";
import AddToQueueDialog from "src/sections/annotations/queues/components/add-to-queue-dialog";
import AddDataset from "src/components/traceDetailDrawer/addToDataset/add-dataset";
import { LLM_TABS } from "src/sections/projects/LLMTracing/common";
import useImagineStore from "src/components/imagine/useImagineStore";
import {
  useGetSavedViews,
  useDeleteSavedView,
  useReorderSavedViews,
} from "src/api/project/saved-views";

import VoiceDrawerHeader from "./VoiceDrawerHeader";
import VoiceLeftPanel from "./VoiceLeftPanel";
import VoiceRightPanel from "./VoiceRightPanel";

const VOICE_IMAGINE_PROMPTS = [
  { label: "Summarize this call", icon: "mdi:text-box-outline" },
  { label: "Show the conversation flow", icon: "mdi:message-text-outline" },
  { label: "Analyze speaker turn distribution", icon: "mdi:chart-bar" },
  { label: "What's the latency breakdown?", icon: "mdi:timer-outline" },
  { label: "Show the cost breakdown", icon: "mdi:currency-usd" },
  {
    label: "Evaluate call quality",
    icon: "mdi:checkbox-marked-circle-outline",
  },
];

/**
 * Revamped voice observability drawer — inspired by TraceDetailDrawerV2.
 * Content-only: expects to be rendered inside an outer MUI Drawer, matching
 * the existing TestDetailSideDrawer / CallLogDetailDrawer wrappers.
 *
 * Layout:
 *   ┌─ Header ─────────────────────────────────┐
 *   ├─ Toolbar (tabs incl. Imagine views)──────┤
 *   │  ┌─────────────┐  │  ┌─────────────────┐ │
 *   │  │ Recording + │  │  │ Call Analytics  │ │
 *   │  │ Transcript/ │  │  │ / Evals / Attrs │ │
 *   │  │ Path        │  │  │ / Annotations   │ │
 *   │  └─────────────┘  │  └─────────────────┘ │
 *   └──────────────────────────────────────────┘
 */
const VoiceDetailDrawerV2 = ({
  data,
  onClose,
  onPrev,
  onNext,
  hasPrev,
  hasNext,
  isFetching,
  onAnnotate,
  onCompareBaseline,
  scenarioId,
  isLoading = false,
  initialFullscreen = false,
  // When embedded (e.g. inside the annotation workspace content panel),
  // hide the outer drawer chrome — the DrawerToolbar (Imagine tabs) and
  // the VoiceDrawerHeader (close/nav/fullscreen bar) — and just render
  // the call body so it fits the host's layout.
  embedded = false,
  hideAnnotationTab=false
}) => {
  const queryClient = useQueryClient();
  const { observeId } = useParams();
  const projectId = observeId || data?.project_id;

  const [leftPanelWidth, setLeftPanelWidth] = useState(50); // percentage
  const [isFullscreen, setIsFullscreen] = useState(initialFullscreen);
  const [shareDialogOpen, setShareDialogOpen] = useState(false);
  const [tagsAnchorEl, setTagsAnchorEl] = useState(null);
  const [queueAnchorEl, setQueueAnchorEl] = useState(null);
  const [datasetDrawerOpen, setDatasetDrawerOpen] = useState(false);
  const [deleteTabId, setDeleteTabId] = useState(null); // for confirm dialog
  // Outer drawer width (vw). Matches trace drawer default and is draggable.
  const [drawerWidth, setDrawerWidth] = useState(60);

  // ── Saved views (Imagine tabs) ────────────────────────────────────────────
  const { data: savedViewsData } = useGetSavedViews(projectId);
  const { mutate: deleteSavedView } = useDeleteSavedView(projectId);
  const { mutate: reorderSavedViews } = useReorderSavedViews(projectId);

  const customViews = savedViewsData?.custom_views || [];

  const [activeDrawerTab, setActiveDrawerTab] = useState("voice");

  const drawerTabs = useMemo(() => {
    const tabs = [
      {
        id: "voice",
        label: "Voice",
        icon: "mdi:phone-outline",
        isDefault: true,
      },
    ];
    customViews
      .filter((v) => (v.tab_type || v.tabType) === "imagine")
      .forEach((v) => {
        tabs.push({
          id: v.id,
          label: v.name,
          icon: "mdi:creation",
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
    if (tabId === "voice") return;
    // Unsaved Imagine tab — just switch back, no API call needed
    if (tabId === "__new_imagine__") {
      setActiveDrawerTab("voice");
      return;
    }
    setDeleteTabId(tabId);
  }, []);

  const handleCreateView = useCallback((e) => {
    // Saving a "voice" view is not yet supported — only Imagine tabs are
    // persisted via the ImagineTab's own Save button.
    e?.stopPropagation?.();
    enqueueSnackbar("Use Imagine to save a custom view", { variant: "info" });
  }, []);

  const handleReorderTabs = useCallback(
    (orderedIds) => {
      const imagineIdSet = new Set(orderedIds);
      const imaginePosById = Object.fromEntries(
        orderedIds.map((id, i) => [id, i]),
      );
      const nonImagine = customViews.filter((v) => !imagineIdSet.has(v.id));
      const merged = [
        ...nonImagine.map((v, i) => ({ id: v.id, position: i })),
        ...orderedIds.map((id) => ({
          id,
          position: nonImagine.length + imaginePosById[id],
        })),
      ];
      reorderSavedViews({ project_id: projectId, order: merged });
    },
    [customViews, projectId, reorderSavedViews],
  );

  // ── Drag handler for resizable divider ────────────────────────────────────
  const handleDragStart = useCallback(
    (e) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = leftPanelWidth;
      const container = e.target.closest("[data-voice-drawer-content]");
      if (!container) return;
      const containerWidth = container.offsetWidth;

      const onMouseMove = (moveEvent) => {
        const diff = moveEvent.clientX - startX;
        const newPct = startWidth + (diff / containerWidth) * 100;
        setLeftPanelWidth(Math.min(70, Math.max(25, newPct)));
      };
      const onMouseUp = () => {
        document.removeEventListener("mousemove", onMouseMove);
        document.removeEventListener("mouseup", onMouseUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      document.addEventListener("mousemove", onMouseMove);
      document.addEventListener("mouseup", onMouseUp);
    },
    [leftPanelWidth],
  );

  // ── Download raw data ─────────────────────────────────────────────────────
  const handleDownload = useCallback(() => {
    if (!data) return;
    try {
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `voice-call-${data?.id || data?.trace_id || "unknown"}.json`;
      a.click();
      URL.revokeObjectURL(url);
      enqueueSnackbar("Call data downloaded", { variant: "success" });
    } catch {
      enqueueSnackbar("Failed to download call data", { variant: "error" });
    }
  }, [data]);

  // Unified action handler — routes action dropdown items
  const handleVoiceAction = useCallback(
    (actionId) => {
      switch (actionId) {
        case "annotate":
          onAnnotate?.();
          break;
        case "download":
          handleDownload();
          break;
        case "tags": {
          // Anchor to the actions dropdown button. document.activeElement is
          // the clicked menu item at this point; walk up to its parent Menu
          // paper, which sits next to the dropdown trigger in the layout.
          const el =
            document.querySelector("[data-voice-actions-button]") ||
            document.body;
          setTagsAnchorEl(el);
          break;
        }
        case "queue": {
          const el =
            document.querySelector("[data-voice-actions-button]") ||
            document.body;
          setQueueAnchorEl(el);
          break;
        }
        case "dataset":
          setDatasetDrawerOpen(true);
          break;
        default:
          break;
      }
    },
    [onAnnotate, handleDownload],
  );

  // ── Imagine tab config ────────────────────────────────────────────────────
  const activeTabConfig = drawerTabs.find(
    (t) => t.id === activeDrawerTab,
  )?.config;

  const imagineReadOnly =
    activeDrawerTab !== "__new_imagine__" &&
    drawerTabs.find((t) => t.id === activeDrawerTab)?.tabType === "imagine";

  return (
    <Box
      sx={{
        position: "relative",
        display: "flex",
        flexDirection: "column",
        width: isFullscreen ? "100vw" : `${drawerWidth}vw`,
        height: "100vh",
        minHeight: "100vh",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      {/* Left-edge resize handle — drag to resize the drawer. Mirrors the
          exact interaction pattern of TraceDetailDrawerV2. Hidden in
          fullscreen since there's nothing to resize into. */}
      {!isFullscreen && !initialFullscreen && (
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

      {/* Drawer header — drops in embedded mode so the host view
          (e.g. annotation workspace) owns its close/nav chrome. */}
      {!embedded && (
        <VoiceDrawerHeader
          callId={data?.provider_call_id || data?.id || data?.trace_id}
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
            initialFullscreen ||
            !projectId ||
            !(data?.trace_id || data?.id)
              ? undefined
              : () => {
                  // Prefer the canonical trace id so the full page takes
                  // the observe fetch path; fall back to the CallExecution
                  // id for simulate-only calls — VoiceFullPage resolves
                  // either via its built-in fetch fallback.
                  const id = data.trace_id || data.id;
                  window.open(
                    `/dashboard/observe/${projectId}/voice/${id}`,
                    "_blank",
                  );
                }
          }
          onDownload={handleDownload}
          onShare={() => setShareDialogOpen(true)}
        />
      )}

      {/* Tab bar (Voice + Imagine views) — always shown. In embedded mode
          the host owns the close/nav chrome, but users still need the
          Imagine tabs to pivot between custom views. */}
      <DrawerToolbar
        tabs={drawerTabs}
        activeTabId={activeDrawerTab}
        onTabChange={setActiveDrawerTab}
        onCloseTab={handleCloseTab}
        onCreateTab={handleCreateView}
        onCreateImagineTab={handleCreateImagineTab}
        onReorderTabs={handleReorderTabs}
        hideFilter
        hideDisplay
      />

      {/* Main content */}
      <Box
        data-voice-drawer-content
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: isImagineActive ? "column" : "row",
          overflow: "hidden",
          minHeight: 0,
        }}
      >
        {isLoading || isFetching === "initial" ? (
          <Box
            sx={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <CircularProgress size={28} />
          </Box>
        ) : isImagineActive ? (
          <ImagineTab
            traceId={data?.trace_id || data?.id}
            projectId={projectId}
            entityType="voice"
            suggestedPrompts={VOICE_IMAGINE_PROMPTS}
            traceData={{
              spans: data?.observation_span || data?.observation_spans || [],
              summary: data?.summary || {},
              transcript: data?.transcript || [],
              trace: {
                id: data?.trace_id || data?.id,
                transcript: data?.transcript,
                recordings: data?.recordings,
                call_summary: data?.call_summary,
                provider: data?.provider,
                status: data?.status,
                module: data?.module,
                customerLatencyMetrics: data?.customer_latency_metrics,
                customerCostBreakdown: data?.customer_cost_breakdown,
                evalOutputs: data?.eval_metrics,
                endedReason: data?.ended_reason,
                callType: data?.call_type,
                phoneNumber: data?.phone_number,
              },
            }}
            readOnly={imagineReadOnly}
            savedViewId={imagineReadOnly ? activeDrawerTab : null}
            savedWidgets={activeTabConfig?.widgets}
            savedConversationId={
              activeTabConfig?.conversation_id ||
              activeTabConfig?.conversationId
            }
            onSaved={() =>
              queryClient.invalidateQueries({
                queryKey: ["saved-views", projectId],
              })
            }
          />
        ) : (
          <>
            {/* Left Panel — no padding or border; VoiceLeftPanel manages
                its own internal padding. The vertical divider between
                panels is provided by the resize handle column below. */}
            <Box
              sx={{
                width: `${leftPanelWidth}%`,
                minWidth: 0,
                overflow: "hidden",
                display: "flex",
                flexDirection: "column",
                borderRight: "1px solid",
                borderColor: "divider",
              }}
            >
              <VoiceLeftPanel data={data} scenarioId={scenarioId} />
            </Box>

            {/* Resizable divider */}
            <Box
              onMouseDown={handleDragStart}
              sx={{
                width: 8,
                cursor: "col-resize",
                flexShrink: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                "&:hover .divider-dots": { opacity: 1 },
                "&:active .divider-dots": { opacity: 1 },
              }}
            >
              <Stack
                className="divider-dots"
                spacing={0.4}
                sx={{ opacity: 0.4, transition: "opacity 150ms" }}
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
              </Stack>
            </Box>

            {/* Right Panel — no padding; VoiceRightPanel manages its own. */}
            <Box
              sx={{
                flex: 1,
                minWidth: 0,
                overflow: "hidden",
                display: "flex",
                flexDirection: "column",
              }}
            >
              <VoiceRightPanel
                data={data}
                onCompareBaseline={onCompareBaseline}
                onAction={handleVoiceAction}
                hideAnnotationTab={hideAnnotationTab}
              />
            </Box>
          </>
        )}
      </Box>

      {/* Share dialog — voice calls share via trace_id, same backend as
          the trace drawer. The fallback URL points at the voice full-page
          route so authenticated recipients land directly on the voice UI. */}
      {(data?.trace_id || data?.id) && (
        <ShareDialog
          open={shareDialogOpen}
          onClose={() => setShareDialogOpen(false)}
          resourceType="trace"
          resourceId={data?.trace_id || data?.id}
          fallbackShareUrl={
            projectId && (data?.trace_id || data?.id)
              ? `${window.location.origin}/dashboard/observe/${projectId}/voice/${data?.trace_id || data?.id}`
              : undefined
          }
        />
      )}

      {/* Add tags popover — reuses the exact component the trace drawer
          uses. Voice calls are traces, so traceId here is the call's
          trace_id. Tags written via this popover are persisted on the
          trace record and surfaced back in CallDetailsBar on the next
          fetch. We gate on `trace_id` only (no `data.id` fallback) —
          simulate-mode call_execution ids aren't traces and the tag
          PATCH would return "Trace not found" (TH-4288). */}
      {data?.trace_id && (
        <AddTagsPopover
          anchorEl={tagsAnchorEl}
          open={Boolean(tagsAnchorEl)}
          onClose={() => setTagsAnchorEl(null)}
          traceId={data.trace_id}
          currentTags={data?.tags || data?.trace?.tags || []}
          onSuccess={() =>
            queryClient.invalidateQueries({ queryKey: ["voiceCallDetail"] })
          }
        />
      )}

      {/* Add to annotation queue — same popover the trace drawer uses.
          Voice calls are traces, so sourceType = "trace" and sourceId
          is the call's trace_id. */}
      {/* Annotation queues accept both `trace` and `call_execution` source
          types (model_hub AnnotationQueueItem). Prefer the canonical trace
          when it exists — otherwise fall back to the CallExecution id so
          simulate-only calls without observability can still be queued. */}
      <AddToQueueDialog
        anchorEl={queueAnchorEl}
        onClose={() => setQueueAnchorEl(null)}
        sourceType={data?.trace_id ? "trace" : "call_execution"}
        sourceIds={
          data?.trace_id ? [data.trace_id] : data?.id ? [data.id] : []
        }
        itemName={data?.customer_name || "Voice call"}
      />

      {/* Move to dataset — a voice call is a trace-level entity, so we send
          `trace_ids` and the TRACE tab. The backend derives `project` from
          those trace(s) when the client doesn't pass one, so there's no
          need to thread a projectId through here. The action itself is
          gated on `data.trace_id` in CallDetailsBar — if it's null we never
          open this drawer. */}
      <AddDataset
        handleClose={() => setDatasetDrawerOpen(false)}
        actionToDataset={datasetDrawerOpen}
        currentTab={LLM_TABS.TRACE}
        selectedTraces={data?.trace_id ? [data.trace_id] : []}
      />

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
                  if (activeDrawerTab === tabId) setActiveDrawerTab("voice");
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
};

VoiceDetailDrawerV2.propTypes = {
  data: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onPrev: PropTypes.func,
  onNext: PropTypes.func,
  hasPrev: PropTypes.bool,
  hasNext: PropTypes.bool,
  isFetching: PropTypes.string,
  onAnnotate: PropTypes.func,
  onCompareBaseline: PropTypes.func,
  scenarioId: PropTypes.string,
  isLoading: PropTypes.bool,
  initialFullscreen: PropTypes.bool,
  embedded: PropTypes.bool,
  hideAnnotationTab: PropTypes.bool,
};

export default VoiceDetailDrawerV2;
