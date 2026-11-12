import React, { useCallback, useEffect } from "react";
import PropTypes from "prop-types";
import { useLocation } from "react-router-dom";
import Box from "@mui/material/Box";
import Drawer from "@mui/material/Drawer";
import useFalconStore from "./store/useFalconStore";
import { captureFalconRequest } from "./store/falconRequestGuard";
import {
  beginFalconSubmission,
  clearFalconSubmission,
} from "./store/falconSubmission";
import useFalconSocket from "./hooks/useFalconSocket";
import useFalconCatalog from "./hooks/useFalconCatalog";
import { useFalconContext } from "./hooks/useFalconContext";
import { createConversation } from "./hooks/useFalconAPI";
import FalconAIHeader from "./components/FalconAIHeader";
import MessageList from "./components/MessageList";
import ChatInput from "./components/ChatInput";
import SkillPicker from "./components/SkillPicker";

const SIDEBAR_WIDTH = 420;

// variant "docked": a 420 px column beside the page (the desktop shell).
// variant "overlay": a drawer over the page, never wider than the screen (the
// compact Settings shell below md, where a docked column pushes the page
// sideways).
export default function FalconAISidebar({ variant = "docked" }) {
  const isSidebarOpen = useFalconStore((s) => s.isSidebarOpen);
  const closeSidebar = useFalconStore((s) => s.closeSidebar);
  const currentConversationId = useFalconStore((s) => s.currentConversationId);
  const setCurrentConversation = useFalconStore(
    (s) => s.setCurrentConversation,
  );
  const addMessage = useFalconStore((s) => s.addMessage);
  const setStreaming = useFalconStore((s) => s.setStreaming);
  const setPendingPrompt = useFalconStore((s) => s.setPendingPrompt);
  const messages = useFalconStore((s) => s.messages);

  const { pathname } = useLocation();
  const { sendChat, sendStop, sendFeedback } = useFalconSocket();
  const context = useFalconContext();

  // Auto-close sidebar when navigating to Falcon AI full page
  useEffect(() => {
    if (pathname.startsWith("/dashboard/falcon-ai") && isSidebarOpen) {
      closeSidebar();
    }
  }, [pathname, isSidebarOpen, closeSidebar]);

  const loadSkillsAndConnectors = useFalconCatalog(isSidebarOpen);

  const handleSend = useCallback(
    async (text) => {
      const isCurrent = captureFalconRequest({ conversation: true });
      const attachedFiles = useFalconStore.getState().attachedFiles;
      const submissionId = beginFalconSubmission(
        text,
        attachedFiles,
        isCurrent,
      );
      let convId = currentConversationId;

      if (!convId) {
        try {
          const resp = await createConversation(
            text.slice(0, 50),
            context.page,
            { submissionId },
          );
          if (
            !isCurrent() ||
            useFalconStore.getState().pendingDraft?.id !== submissionId
          ) {
            clearFalconSubmission(submissionId);
            return;
          }
          convId = resp.result?.id || resp.id;
          setCurrentConversation(convId);
        } catch {
          clearFalconSubmission(submissionId);
          return;
        }
      }

      const fileIds = attachedFiles.map((f) => f.id);

      const userMsg = {
        id: `user-${Date.now()}`,
        role: "user",
        content: text,
        files: attachedFiles.map((f) => ({
          id: f.id,
          name: f.name,
          size: f.size,
          content_type: f.content_type,
          url: f.url,
        })),
        created_at: new Date().toISOString(),
      };
      addMessage(userMsg);
      useFalconStore.getState().clearAttachedFiles();

      const assistantMsgId = `assistant-${Date.now()}`;
      addMessage({
        id: assistantMsgId,
        role: "assistant",
        content: "",
        created_at: new Date().toISOString(),
      });
      setStreaming(true, assistantMsgId);

      sendChat(text, convId, context, fileIds, attachedFiles, submissionId);
    },
    [
      currentConversationId,
      context,
      setCurrentConversation,
      addMessage,
      setStreaming,
      sendChat,
    ],
  );

  const handleQuickAction = useCallback(
    (prompt) => {
      setPendingPrompt(prompt);
    },
    [setPendingPrompt],
  );

  if (!isSidebarOpen) return null;

  const overlay = variant === "overlay";

  const panel = (
    <Box
      data-testid="falcon-ai-sidebar"
      sx={{
        width: overlay ? "100%" : SIDEBAR_WIDTH,
        minWidth: overlay ? 0 : SIDEBAR_WIDTH,
        height: overlay ? "100%" : "100vh",
        display: "flex",
        flexDirection: "column",
        borderLeft: 1,
        borderColor: "divider",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <FalconAIHeader onClose={closeSidebar} mode="sidebar" />
      <Box
        sx={{
          flex: 1,
          overflow: "auto",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <MessageList
          onQuickAction={handleQuickAction}
          onFeedback={sendFeedback}
        />
        {messages.length === 0 && (
          <Box sx={{ px: 2, pb: 2 }}>
            <SkillPicker onSkillsChanged={loadSkillsAndConnectors} />
          </Box>
        )}
      </Box>
      <ChatInput onSend={handleSend} onStop={sendStop} />
    </Box>
  );

  if (!overlay) return panel;

  return (
    <Drawer
      anchor="right"
      open
      onClose={closeSidebar}
      PaperProps={{ sx: { width: SIDEBAR_WIDTH, maxWidth: "100vw" } }}
    >
      {panel}
    </Drawer>
  );
}

FalconAISidebar.propTypes = {
  variant: PropTypes.oneOf(["docked", "overlay"]),
};

FalconAISidebar.SIDEBAR_WIDTH = SIDEBAR_WIDTH;
