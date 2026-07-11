import React, { useCallback, useEffect } from "react";
import { useLocation } from "react-router-dom";
import Box from "@mui/material/Box";
import { alpha, useTheme } from "@mui/material/styles";
import useFalconStore from "./store/useFalconStore";
import useFalconSocket from "./hooks/useFalconSocket";
import { useFalconContext } from "./hooks/useFalconContext";
import {
  createConversation,
  listSkills,
  fetchConnectors,
} from "./hooks/useFalconAPI";
import FalconAIHeader from "./components/FalconAIHeader";
import MessageList from "./components/MessageList";
import ChatInput from "./components/ChatInput";
import SkillPicker from "./components/SkillPicker";
import prependConversation from "./helpers/prependConversation";

const SIDEBAR_WIDTH = 420;

export default function FalconAISidebar() {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const isSidebarOpen = useFalconStore((s) => s.isSidebarOpen);
  const closeSidebar = useFalconStore((s) => s.closeSidebar);
  const currentConversationId = useFalconStore((s) => s.currentConversationId);
  const setCurrentConversation = useFalconStore(
    (s) => s.setCurrentConversation,
  );
  const addMessage = useFalconStore((s) => s.addMessage);
  const setStreaming = useFalconStore((s) => s.setStreaming);
  const setPendingPrompt = useFalconStore((s) => s.setPendingPrompt);
  const setSkills = useFalconStore((s) => s.setSkills);
  const setConnectors = useFalconStore((s) => s.setConnectors);
  const messages = useFalconStore((s) => s.messages);

  const { pathname } = useLocation();
  const { sendChat, sendStop, sendFeedback, sendConfirmAction } =
    useFalconSocket();
  const context = useFalconContext();

  // Auto-close sidebar when navigating to Falcon AI full page
  useEffect(() => {
    if (pathname.startsWith("/dashboard/falcon-ai") && isSidebarOpen) {
      closeSidebar();
    }
  }, [pathname, isSidebarOpen, closeSidebar]);

  const loadSkillsAndConnectors = useCallback(async () => {
    try {
      const skillData = await listSkills();
      setSkills(skillData.results || skillData || []);
    } catch {
      // silent
    }
    try {
      const connData = await fetchConnectors();
      setConnectors(connData.results || connData || []);
    } catch {
      // silent
    }
  }, [setSkills, setConnectors]);

  useEffect(() => {
    if (isSidebarOpen) {
      loadSkillsAndConnectors();
    }
  }, [isSidebarOpen, loadSkillsAndConnectors]);

  const handleSend = useCallback(
    async (text) => {
      let convId = currentConversationId;

      if (!convId) {
        try {
          const resp = await createConversation(
            text.slice(0, 50),
            context.page,
          );
          const newConv = resp.result || resp;
          convId = newConv.id;
          setCurrentConversation(convId);
          // Register in the conversations list so title_generated finds a
          // row to update (sidebar-born chats previously lost their titles)
          prependConversation(newConv, text.slice(0, 50));
        } catch {
          return;
        }
      }

      const attachedFiles = useFalconStore.getState().attachedFiles;
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

      sendChat(text, convId, context, fileIds);
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

  return (
    <Box
      sx={{
        width: SIDEBAR_WIDTH,
        minWidth: SIDEBAR_WIDTH,
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        borderLeft: 1,
        borderColor: isDark
          ? alpha(theme.palette.common.white, 0.08)
          : alpha(theme.palette.common.black, 0.08),
        bgcolor: isDark ? "background.default" : "background.paper",
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
          onConfirmAction={sendConfirmAction}
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
}

FalconAISidebar.SIDEBAR_WIDTH = SIDEBAR_WIDTH;
