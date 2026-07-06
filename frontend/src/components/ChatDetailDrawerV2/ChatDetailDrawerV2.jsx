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

import VoiceDrawerHeader from "src/components/VoiceDetailDrawerV2/VoiceDrawerHeader";
import ChatLeftPanel from "./ChatLeftPanel";
import ChatRightPanel from "./ChatRightPanel";
import ChatCompareView from "./Compare/ChatCompareView";
import { CHAT_IMAGINE_PROMPTS, CHAT_EXPORT_FIELDS } from "./constants";

// Chat-specific peer of VoiceDetailDrawerV2 — content-only drawer body
// (the header/width/fullscreen chrome is provided by TestDetailSideDrawer).
const ChatDetailDrawerV2 = ({
  data,
  onClose,
  onPrev,
  onNext,
  hasPrev,
  hasNext,
  isFetching,
  onAnnotate,
  onCompareBaseline,
  onExitCompare,
  compareReplay = false,
  scenarioId,
  isLoading = false,
  initialFullscreen = false,
  // When embedded (e.g. inside the annotation workspace content panel),
  // hide the outer drawer chrome — the DrawerToolbar (Imagine tabs)
  // and the VoiceDrawerHeader (close/nav/fullscreen) — and just render
  // the chat body so it fits the host's layout.
  embedded = false,
  // Hide the right-panel "Annotations" tab (e.g. in the annotation
  // workspace, where annotation happens in the host's side panel).
  hideAnnotationTab = false,
}) => {
  const queryClient = useQueryClient();
  const { observeId } = useParams();
  const projectId = observeId || data?.project_id;

  const [leftPanelWidth, setLeftPanelWidth] = useState(50);
  const [isFullscreen, setIsFullscreen] = useState(initialFullscreen);
  const [drawerWidth, setDrawerWidth] = useState(60);

  // Cross-cutting dialog state — same set as voice.
  const [shareDialogOpen, setShareDialogOpen] = useState(false);
  const [tagsAnchorEl, setTagsAnchorEl] = useState(null);
  const [queueAnchorEl, setQueueAnchorEl] = useState(null);
  const [datasetDrawerOpen, setDatasetDrawerOpen] = useState(false);
  const [deleteTabId, setDeleteTabId] = useState(null);

  // ── Saved views / Imagine tabs ───────────────────────────────────────
  const { data: savedViewsData } = useGetSavedViews(projectId);
  const { mutate: deleteSavedView } = useDeleteSavedView(projectId);
  const { mutate: reorderSavedViews } = useReorderSavedViews(projectId);
  // Sort by stored position so the saved tab order is restored on reload
  // (the list API doesn't guarantee position order).
  const customViews = useMemo(
    () =>
      [...(savedViewsData?.custom_views || [])].sort(
        (a, b) => (a.position ?? 0) - (b.position ?? 0),
      ),
    [savedViewsData?.custom_views],
  );

  const [activeDrawerTab, setActiveDrawerTab] = useState("chat");

  const drawerTabs = useMemo(() => {
    const tabs = [
      {
        id: "chat",
        label: "Chat",
        icon: "mdi:message-text-outline",
        isDefault: true,
        visibility: "project",
      },
    ];
    customViews
      .filter((v) => v.tab_type === "imagine")
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
    if (activeDrawerTab === "__new_imagine__") {
      tabs.push({
        id: "__new_imagine__",
        label: "Imagine",
        icon: "mdi:creation",
        isDefault: false,
        tabType: "imagine",
        visibility: "personal",
      });
    }
    return tabs;
  }, [customViews, activeDrawerTab]);

  const isImagineActive =
    activeDrawerTab === "__new_imagine__" ||
    drawerTabs.find((t) => t.id === activeDrawerTab)?.tabType === "imagine";

  const activeTabConfig = drawerTabs.find(
    (t) => t.id === activeDrawerTab,
  )?.config;

  const imagineReadOnly =
    activeDrawerTab !== "__new_imagine__" &&
    drawerTabs.find((t) => t.id === activeDrawerTab)?.tabType === "imagine";

  const handleCreateImagineTab = () => {
    useImagineStore.getState().reset();
    setActiveDrawerTab("__new_imagine__");
  };

  const handleCloseTab = (tabId) => {
    if (tabId === "chat") return;
    if (tabId === "__new_imagine__") {
      setActiveDrawerTab("chat");
      return;
    }
    setDeleteTabId(tabId);
  };

  const handleCreateView = (e) => {
    e?.stopPropagation?.();
    enqueueSnackbar("Use Imagine to save a custom view", { variant: "info" });
  };

  const handleReorderTabs = useCallback(
    (orderedIds) => {
      // Only imagine views reorder here. Reassign just the position slots
      // those views already occupy (in the new order) and leave every other
      // view's stored position untouched, so trace views in ObserveTabBar
      // keep their order.
      const orderedSet = new Set(orderedIds);
      const slots = customViews
        .filter((v) => orderedSet.has(v.id))
        .map((v) => v.position)
        .sort((a, b) => (a ?? 0) - (b ?? 0));
      const order = orderedIds.map((id, i) => ({ id, position: slots[i] ?? i }));
      reorderSavedViews({ project_id: projectId, order });
    },
    [customViews, projectId, reorderSavedViews],
  );

  // ── Drag handler for resizable inner divider ─────────────────────────
  const handleDragStart = useCallback(
    (e) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = leftPanelWidth;
      const container = e.target.closest("[data-chat-drawer-content]");
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

  // ── Download chat data (curated, user-facing fields only) ────────────
  const handleDownload = useCallback(() => {
    if (!data) return;
    try {
      const exportData = Object.fromEntries(
        CHAT_EXPORT_FIELDS.filter((k) => data[k] !== undefined).map((k) => [
          k,
          data[k],
        ]),
      );
      const blob = new Blob([JSON.stringify(exportData, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `chat-${data?.id || data?.trace_id || "unknown"}.json`;
      a.click();
      URL.revokeObjectURL(url);
      enqueueSnackbar("Chat data downloaded", { variant: "success" });
    } catch {
      enqueueSnackbar("Failed to download chat data", { variant: "error" });
    }
  }, [data]);

  // Routes the actions dropdown — full set now (annotate, download,
  // tags, queue, dataset). Anchor for tags/queue popovers is the
  // VoiceActionsDropdown trigger button (it tags itself with the
  // `data-voice-actions-button` attribute).
  const handleChatAction = useCallback(
    (actionId) => {
      switch (actionId) {
        case "annotate":
          onAnnotate?.();
          break;
        case "download":
          handleDownload();
          break;
        case "tags": {
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
      {/* Left-edge resize handle */}
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

      {/* Header + DrawerToolbar — omitted in embedded mode. */}
      {!embedded && (
        <>
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
            onDownload={handleDownload}
            onShare={
              data?.trace_id || data?.id
                ? () => setShareDialogOpen(true)
                : undefined
            }
            // No chat full-page route exists yet — omit `onOpenNewTab`
            // entirely so the header button hides. Wire this up once a
            // route lands.
            idLabel="Chat ID"
            copyTooltip="Copy Chat ID"
            copyToastMessage="Chat ID copied"
          />

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
        </>
      )}

      {/* Main content */}
      <Box
        data-chat-drawer-content
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: isImagineActive || compareReplay ? "column" : "row",
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
        ) : compareReplay ? (
          // Compare body owns the whole content area; back-to-chat
          // affordance lives inside it.
          <ChatCompareView data={data} onBack={onExitCompare} />
        ) : isImagineActive ? (
          <ImagineTab
            traceId={data?.trace_id || data?.id}
            projectId={projectId}
            entityType="chat"
            suggestedPrompts={CHAT_IMAGINE_PROMPTS}
            traceData={{
              spans: data?.observation_span || data?.observation_spans || [],
              summary: data?.summary || {},
              transcript: data?.transcript || [],
              trace: {
                id: data?.trace_id || data?.id,
                transcript: data?.transcript,
                call_summary: data?.call_summary,
                provider: data?.provider,
                status: data?.status,
                module: data?.module,
                customerLatencyMetrics: data?.customer_latency_metrics,
                customerCostBreakdown: data?.customer_cost_breakdown,
                evalOutputs: data?.eval_metrics,
                endedReason: data?.ended_reason,
                callType: data?.call_type,
              },
            }}
            readOnly={imagineReadOnly}
            savedViewId={imagineReadOnly ? activeDrawerTab : null}
            savedWidgets={activeTabConfig?.widgets}
            savedConversationId={activeTabConfig?.conversation_id}
            onSaved={() =>
              queryClient.invalidateQueries({
                queryKey: ["saved-views", projectId],
              })
            }
          />
        ) : (
          <>
            {/* Left Panel */}
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
              <ChatLeftPanel data={data} scenarioId={scenarioId} />
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

            {/* Right Panel */}
            <Box
              sx={{
                flex: 1,
                minWidth: 0,
                overflow: "hidden",
                display: "flex",
                flexDirection: "column",
              }}
            >
              <ChatRightPanel
                data={data}
                onCompareBaseline={onCompareBaseline}
                // No drawer-level Actions dropdown when embedded (e.g. the
                // annotation workspace, which provides its own actions).
                onAction={embedded ? undefined : handleChatAction}
                hideAnnotationTab={hideAnnotationTab}
              />
            </Box>
          </>
        )}
      </Box>

      {/* Share dialog — chat shares by trace_id (same backend as
          voice). Fallback URL is omitted because there's no chat
          full-page route yet. */}
      {(data?.trace_id || data?.id) && (
        <ShareDialog
          open={shareDialogOpen}
          onClose={() => setShareDialogOpen(false)}
          resourceType="trace"
          resourceId={data?.trace_id || data?.id}
        />
      )}

      {/* Add tags popover — only mounted when a real trace exists.
          The trace-tag PATCH 404s for CallExecution ids. */}
      {data?.trace_id && (
        <AddTagsPopover
          anchorEl={tagsAnchorEl}
          open={Boolean(tagsAnchorEl)}
          onClose={() => setTagsAnchorEl(null)}
          traceId={data.trace_id}
          currentTags={data?.tags || data?.trace?.tags || []}
          onSuccess={() =>
            queryClient.invalidateQueries({ queryKey: ["chatCallDetail"] })
          }
        />
      )}

      {/* Add to annotation queue — accepts both `trace` and
          `call_execution` source types, so simulate-only rows still work
          via the CallExecution id fallback. */}
      <AddToQueueDialog
        anchorEl={queueAnchorEl}
        onClose={() => setQueueAnchorEl(null)}
        sourceType={data?.trace_id ? "trace" : "call_execution"}
        sourceIds={data?.trace_id ? [data.trace_id] : data?.id ? [data.id] : []}
        itemName={data?.customer_name || "Chat"}
      />

      {/* Move to dataset — gated by ChatDetailsBar (action is hidden
          when there's no trace_id). The drawer still mounts the dialog
          unconditionally so opening it always works. */}
      <AddDataset
        handleClose={() => setDatasetDrawerOpen(false)}
        actionToDataset={datasetDrawerOpen}
        currentTab={LLM_TABS.TRACE}
        selectedTraces={data?.trace_id ? [data.trace_id] : []}
      />

      {/* Delete saved-view confirmation */}
      <ConfirmDialog
        open={Boolean(deleteTabId)}
        onClose={() => setDeleteTabId(null)}
        title="Delete view"
        content={`Are you sure you want to delete "${
          drawerTabs.find((t) => t.id === deleteTabId)?.label || "this view"
        }"? This action cannot be undone.`}
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
                  if (activeDrawerTab === tabId) setActiveDrawerTab("chat");
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

ChatDetailDrawerV2.propTypes = {
  data: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onPrev: PropTypes.func,
  onNext: PropTypes.func,
  hasPrev: PropTypes.bool,
  hasNext: PropTypes.bool,
  isFetching: PropTypes.string,
  onAnnotate: PropTypes.func,
  onCompareBaseline: PropTypes.func,
  onExitCompare: PropTypes.func,
  compareReplay: PropTypes.bool,
  scenarioId: PropTypes.string,
  isLoading: PropTypes.bool,
  initialFullscreen: PropTypes.bool,
  embedded: PropTypes.bool,
  hideAnnotationTab: PropTypes.bool,
};

export default ChatDetailDrawerV2;
