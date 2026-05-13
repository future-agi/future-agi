import React, {
  useState,
  useCallback,
  useEffect,
  useRef,
  useMemo,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  Divider,
  Popover,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import { useCreateSavedView } from "src/api/project/saved-views";
import useImagineStore from "./useImagineStore";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import WidgetCanvas from "./WidgetCanvas";
import ImagineChatPanel from "./ImagineChatPanel";
import SuggestedPrompts from "./SuggestedPrompts";
import { consolidateAnalysisPrompts } from "./consolidatePrompts";

/**
 * Imagine with Falcon — horizontal split tab inside the trace detail drawer.
 * Left: Widget canvas (rendered visualizations)
 * Right: Falcon chat panel (toggleable)
 */
export default function ImagineTab({
  traceId,
  projectId,
  traceData,
  readOnly,
  savedViewId,
  savedWidgets,
  savedConversationId,
  onSaved,
  entityType = "trace",
  suggestedPrompts,
}) {
  const widgets = useImagineStore((s) => s.widgets);
  const replaceAll = useImagineStore((s) => s.replaceAll);
  const [chatWidth, setChatWidth] = useState(360);
  const [showChat, setShowChat] = useState(!readOnly);

  const chatRef = useRef(null);
  const { mutate: createSavedView, isPending: isSaving } =
    useCreateSavedView(projectId);

  // Load saved widgets when tab changes (savedViewId changes)
  const prevViewIdRef = useRef(savedViewId);
  useEffect(() => {
    const viewChanged = savedViewId !== prevViewIdRef.current;
    prevViewIdRef.current = savedViewId;

    if (savedWidgets?.length && (viewChanged || widgets.length === 0)) {
      replaceAll(savedWidgets);
    }
    if (savedConversationId) {
      useImagineStore.getState().setConversationId(savedConversationId);
    }
    if (savedViewId) {
      useImagineStore.getState().setSavedViewId(savedViewId);
    }
  }, [
    savedViewId,
    savedWidgets,
    savedConversationId,
    widgets.length,
    replaceAll,
  ]);

  // Handle prompt chip selection — sends directly to chat
  const handlePromptSelect = useCallback((text) => {
    setShowChat(true);
    // Small delay to let chat panel mount if it was hidden
    setTimeout(() => {
      chatRef.current?.send(text);
    }, 100);
  }, []);

  // Horizontal resize handler
  const handleResizeStart = useCallback(
    (e) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = chatWidth;
      const onMove = (moveE) => {
        const diff = startX - moveE.clientX;
        setChatWidth(Math.max(280, Math.min(600, startW + diff)));
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
    },
    [chatWidth],
  );

  // --- Save View popover ---
  const [saveAnchorEl, setSaveAnchorEl] = useState(null);
  const [saveName, setSaveName] = useState("");

  // Auto-generate a name from widget titles + first user message
  const suggestedName = useMemo(() => {
    const titles = widgets.map((w) => w.title).filter(Boolean);
    if (titles.length) {
      const combined = titles.slice(0, 3).join(", ");
      return combined.length > 40 ? combined.slice(0, 37) + "..." : combined;
    }
    const messages = useFalconStore.getState().messages;
    const firstUserMsg = messages.find((m) => m.role === "user")?.content;
    if (firstUserMsg) {
      return firstUserMsg.length > 40
        ? firstUserMsg.slice(0, 37) + "..."
        : firstUserMsg;
    }
    return `Imagine View — ${new Date().toLocaleDateString()}`;
  }, [widgets]);

  const handleSaveClick = useCallback(
    (e) => {
      if (!widgets.length) {
        enqueueSnackbar("Nothing to save — build some visualizations first", {
          variant: "info",
        });
        return;
      }
      setSaveName(suggestedName);
      setSaveAnchorEl(e.currentTarget);
    },
    [widgets, suggestedName],
  );

  const handleSaveConfirm = useCallback(() => {
    if (!projectId) {
      enqueueSnackbar("Cannot save — project context is missing", {
        variant: "error",
      });
      setSaveAnchorEl(null);
      return;
    }
    const name = saveName.trim() || suggestedName;
    const messages = useFalconStore.getState().messages;
    const consolidatedWidgets = consolidateAnalysisPrompts(widgets, messages);

    createSavedView(
      {
        project_id: projectId,
        name,
        tab_type: "imagine",
        config: {
          widgets: consolidatedWidgets,
          conversation_id: useImagineStore.getState().conversationId,
        },
      },
      {
        onSuccess: () => {
          enqueueSnackbar("View saved as new tab", { variant: "success" });
          setSaveAnchorEl(null);
          onSaved?.();
        },
        onError: () => {
          enqueueSnackbar("Failed to save view", { variant: "error" });
        },
      },
    );
  }, [saveName, suggestedName, widgets, projectId, createSavedView, onSaved]);

  // Empty state with suggested prompts
  const emptyState = (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 2,
        px: 3,
      }}
    >
      <Box
        sx={{
          width: 64,
          height: 64,
          borderRadius: "50%",
          background: "linear-gradient(135deg, #7B56DB 0%, #1ABCFE 100%)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          opacity: 0.8,
        }}
      >
        <Iconify icon="mdi:creation" width={32} sx={{ color: "#fff" }} />
      </Box>

      <Typography fontSize={15} fontWeight={600} color="text.primary">
        Imagine with Falcon
      </Typography>

      <Typography
        fontSize={12}
        color="text.secondary"
        textAlign="center"
        maxWidth={360}
        lineHeight={1.6}
      >
        Describe what you want to see and Falcon will build interactive
        visualizations from your trace data — charts, tables, graphs, anything.
      </Typography>

      <SuggestedPrompts
        onSelect={handlePromptSelect}
        prompts={suggestedPrompts}
      />
    </Box>
  );

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* Toolbar — Save View + Falcon toggle */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
          gap: 0.75,
          px: 1.5,
          py: 0.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
          minHeight: 36,
        }}
      >
        {/* Save View */}
        <ButtonBase
          onClick={handleSaveClick}
          disabled={isSaving || !widgets.length}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            px: 1,
            py: 0.25,
            borderRadius: "4px",
            border: "1px solid",
            borderColor: "divider",
            fontSize: 11,
            fontWeight: 500,
            color: widgets.length ? "text.primary" : "text.disabled",
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <Iconify icon="mdi:content-save-outline" width={14} />
          {isSaving ? "Saving..." : "Save View"}
        </ButtonBase>

        {/* Falcon toggle */}
        <ButtonBase
          onClick={() => setShowChat((prev) => !prev)}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            px: 1,
            py: 0.25,
            borderRadius: "4px",
            border: "1px solid",
            borderColor: showChat ? "primary.main" : "divider",
            fontSize: 11,
            fontWeight: 500,
            color: showChat ? "primary.main" : "text.secondary",
            bgcolor: showChat ? "rgba(123,86,219,0.04)" : "transparent",
            "&:hover": {
              bgcolor: showChat ? "rgba(123,86,219,0.08)" : "action.hover",
            },
          }}
        >
          <Iconify icon="mdi:creation" width={14} />
          Falcon
        </ButtonBase>
      </Box>

      {/* Main content — horizontal split: canvas left, chat right */}
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "row",
          overflow: "hidden",
          minHeight: 0,
        }}
      >
        {/* Widget Canvas — takes remaining space */}
        <Box sx={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
          <WidgetCanvas
            widgets={widgets}
            traceData={traceData}
            emptyState={emptyState}
            chatRef={chatRef}
            traceId={traceId}
            projectId={projectId}
          />
        </Box>

        {/* Chat panel — right side, toggleable */}
        {showChat && (
          <>
            {/* Resizable vertical divider */}
            <Box
              onMouseDown={handleResizeStart}
              sx={{
                width: 6,
                cursor: "col-resize",
                flexShrink: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                borderLeft: "1px solid",
                borderColor: "divider",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "3px",
                  opacity: 0.4,
                }}
              >
                {[0, 1, 2, 3, 4].map((i) => (
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

            {/* Chat panel */}
            <Box sx={{ width: chatWidth, flexShrink: 0, overflow: "hidden" }}>
              <ImagineChatPanel
                ref={chatRef}
                traceId={traceId}
                projectId={projectId}
                entityType={entityType}
              />
            </Box>
          </>
        )}
      </Box>

      {/* Save View popover — anchored to the Save View button */}
      <Popover
        open={Boolean(saveAnchorEl)}
        anchorEl={saveAnchorEl}
        onClose={() => setSaveAnchorEl(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: {
              width: 280,
              borderRadius: "4px",
              border: "1px solid",
              borderColor: "divider",
              boxShadow: "1px 1px 12px 10px rgba(0,0,0,0.04)",
              p: 1,
              mt: 0.5,
            },
          },
        }}
      >
        <Box sx={{ px: 0.5, py: 0.25 }}>
          <Typography
            sx={{
              fontSize: 14,
              fontWeight: 600,
              color: "text.primary",
              fontFamily: "'IBM Plex Sans', sans-serif",
            }}
          >
            Save view
          </Typography>
          <Typography
            sx={{
              fontSize: 11,
              color: "text.secondary",
              fontFamily: "'IBM Plex Sans', sans-serif",
              mt: 0.25,
            }}
          >
            This view will adapt to any trace you open.
          </Typography>
        </Box>

        <Divider sx={{ my: 1 }} />

        <Box sx={{ px: 0.5, py: 0.25 }}>
          <TextField
            fullWidth
            size="small"
            placeholder="View name"
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && saveName.trim()) handleSaveConfirm();
            }}
            autoFocus
            InputProps={{
              sx: {
                fontSize: 13,
                fontFamily: "'IBM Plex Sans', sans-serif",
                borderRadius: "4px",
              },
            }}
          />
        </Box>

        <Divider sx={{ mt: 1.5, mb: 0.5 }} />

        <Box
          sx={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 1,
            px: 0.5,
            pb: 0.5,
          }}
        >
          <Button
            size="small"
            variant="outlined"
            onClick={() => setSaveAnchorEl(null)}
            sx={{
              textTransform: "none",
              fontSize: 12,
              fontWeight: 500,
              borderColor: "divider",
              color: "text.primary",
              borderRadius: "2px",
              px: 2,
              py: 0.25,
            }}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            onClick={handleSaveConfirm}
            disabled={!saveName.trim() || isSaving}
            sx={{
              textTransform: "none",
              fontSize: 12,
              fontWeight: 500,
              borderRadius: "2px",
              px: 2,
              py: 0.25,
              bgcolor: saveName.trim() ? "primary.main" : "action.disabled",
              color: "white",
              "&:hover": {
                bgcolor: saveName.trim() ? "primary.dark" : "action.disabled",
              },
              "&.Mui-disabled": {
                bgcolor: "action.disabled",
                color: "text.disabled",
              },
            }}
          >
            {isSaving ? "Saving..." : "Save view"}
          </Button>
        </Box>
      </Popover>
    </Box>
  );
}

ImagineTab.propTypes = {
  traceId: PropTypes.string,
  projectId: PropTypes.string,
  traceData: PropTypes.object,
  readOnly: PropTypes.bool,
  savedViewId: PropTypes.string,
  savedWidgets: PropTypes.array,
  savedConversationId: PropTypes.string,
  onSaved: PropTypes.func,
  entityType: PropTypes.oneOf(["trace", "voice"]),
  suggestedPrompts: PropTypes.array,
};
