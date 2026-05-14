import React, { useCallback, useState } from "react";
import PropTypes from "prop-types";
import { Box, CircularProgress, Stack } from "@mui/material";
import { enqueueSnackbar } from "notistack";

import VoiceDrawerHeader from "src/components/VoiceDetailDrawerV2/VoiceDrawerHeader";
import ChatLeftPanel from "./ChatLeftPanel";
import ChatRightPanel from "./ChatRightPanel";

/**
 * Chat-specific peer of `VoiceDetailDrawerV2`. Shares the same outer shell
 * (header, resizable drawer width, resizable inner divider, fullscreen)
 * so chat and voice simulations look like sibling surfaces.
 *
 * Intentionally narrower scope than the voice drawer — out of scope for
 * this PR (tracked as follow-ups): Imagine/Saved Views tabs, Share dialog,
 * Add-to-dataset, Add-to-queue, full-page chat route, and tag editing on
 * trace records.
 *
 * Content-only: rendered inside the outer MUI flex container provided by
 * `TestDetailSideDrawer`, same way `VoiceDetailDrawerV2` is.
 *
 * Layout:
 *   ┌─ Header ─────────────────────────────────┐
 *   │  ┌─────────────┐  │  ┌─────────────────┐ │
 *   │  │ Transcript/ │  │  │ Call Analytics  │ │
 *   │  │ Path tabs   │  │  │ / Evals / ...   │ │
 *   │  └─────────────┘  │  └─────────────────┘ │
 *   └──────────────────────────────────────────┘
 */
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
  scenarioId,
  isLoading = false,
  initialFullscreen = false,
  // When embedded (e.g. inside the annotation workspace content panel),
  // hide the outer drawer chrome — the header bar — and just render the
  // chat body so it fits the host's layout. Mirrors VoiceDetailDrawerV2.
  embedded = false,
}) => {
  const [leftPanelWidth, setLeftPanelWidth] = useState(50); // percentage
  const [isFullscreen, setIsFullscreen] = useState(initialFullscreen);
  const [drawerWidth, setDrawerWidth] = useState(60);

  // ── Drag handler for resizable inner divider ──────────────────────────
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

  // ── Download raw data ─────────────────────────────────────────────────
  const handleDownload = useCallback(() => {
    if (!data) return;
    try {
      const blob = new Blob([JSON.stringify(data, null, 2)], {
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

  // Unified action handler — routes the Actions dropdown. Only two
  // actions are wired for chat in this PR: annotate + download.
  const handleChatAction = useCallback(
    (actionId) => {
      switch (actionId) {
        case "annotate":
          onAnnotate?.();
          break;
        case "download":
          handleDownload();
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
      {/* Left-edge resize handle — drag to resize the drawer. Matches
          VoiceDetailDrawerV2's drag-to-resize interaction. */}
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

      {/* Header — omitted in embedded mode so the host view owns its own chrome. */}
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
          onDownload={handleDownload}
          // Relabel the shared header row / tooltip / toast for chat.
          idLabel="Chat ID"
          copyTooltip="Copy Chat ID"
          copyToastMessage="Chat ID copied"
          // Share + open-in-new-tab are voice-only for now; omit so those
          // header buttons don't render.
        />
      )}

      {/* Main content */}
      <Box
        data-chat-drawer-content
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "row",
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
                onAction={handleChatAction}
              />
            </Box>
          </>
        )}
      </Box>
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
  scenarioId: PropTypes.string,
  isLoading: PropTypes.bool,
  initialFullscreen: PropTypes.bool,
  embedded: PropTypes.bool,
};

export default ChatDetailDrawerV2;
