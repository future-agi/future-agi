import React, { useCallback, useEffect, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import Box from "@mui/material/Box";
import useFalconStore from "./store/useFalconStore";
import useFalconSocket from "./hooks/useFalconSocket";
import { useFalconContext } from "./hooks/useFalconContext";
import {
  createConversation,
  getConversation,
  listSkills,
  fetchConnectors,
} from "./hooks/useFalconAPI";
import FalconAIHeader from "./components/FalconAIHeader";
import MessageList from "./components/MessageList";
import ChatInput from "./components/ChatInput";
import ChatListPanel from "./components/ChatListPanel";
import SkillPicker from "./components/SkillPicker";
import CustomizePanel from "./components/CustomizePanel";

export default function FalconAIFullPage() {
  const { conversationId: urlConversationId } = useParams();
  const navigate = useNavigate();

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
  const showCustomize = useFalconStore((s) => s.showCustomize);

  const { sendChat, sendStop, sendFeedback } = useFalconSocket();
  const context = useFalconContext();

  const loadSkillsAndConnectors = useCallback(async () => {
    try {
      const skillData = await listSkills();
      setSkills(skillData.results || skillData || []);
    } catch {
      // silent — skills are optional
    }
    try {
      const connData = await fetchConnectors();
      setConnectors(connData.results || connData || []);
    } catch {
      // silent — connectors are optional
    }
  }, [setSkills, setConnectors]);

  useEffect(() => {
    loadSkillsAndConnectors();
  }, [loadSkillsAndConnectors]);

  const loadedConversationRef = useRef(null);

  // Keep store in sync with browser navigation/back-forward between chats.
  useEffect(() => {
    if (!urlConversationId) {
      loadedConversationRef.current = null;
      return;
    }

    if (loadedConversationRef.current === urlConversationId) {
      return;
    }

    loadedConversationRef.current = urlConversationId;
    if (currentConversationId !== urlConversationId) {
      setCurrentConversation(urlConversationId);
    }
    getConversation(urlConversationId)
      .then((data) => {
        if (loadedConversationRef.current !== urlConversationId) return;
        const conv = data.result || data;
        // Normalize camelCase API response to snake_case for components
        const loadedMessages = (conv.messages || []).map((m) => ({
          ...m,
          tool_calls: m.tool_calls || [],
          completion_card: m.completion_card || null,
          created_at: m.created_at,
        }));
        const store = useFalconStore.getState();
        if (
          store.currentConversationId === urlConversationId &&
          store.isStreaming &&
          store.messages.length > 0 &&
          loadedMessages.length === 0
        ) {
          return;
        }
        store.setMessages(loadedMessages);
      })
      .catch(() => {});
  }, [urlConversationId, currentConversationId, setCurrentConversation]);

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
          // Add to sidebar list immediately
          const store = useFalconStore.getState();
          store.setConversations([
            {
              id: convId,
              title: newConv.title || text.slice(0, 50),
              created_at: new Date().toISOString(),
            },
            ...(store.conversations || []),
          ]);
        } catch {
          return;
        }
      }

      // Capture attached files before clearing
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

      // Send chat BEFORE navigate — navigate can cause re-renders
      sendChat(text, convId, context, fileIds);

      // Update URL after sending (won't remount due to optional route param)
      navigate(`/dashboard/falcon-ai/${convId}`, { replace: true });
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

  return (
    <Box
      sx={{
        display: "flex",
        height: "100%",
        overflow: "hidden",
        bgcolor: "background.default",
      }}
    >
      <ChatListPanel />

      {showCustomize ? (
        <CustomizePanel />
      ) : (
        <Box
          sx={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            minWidth: 0,
          }}
        >
          <FalconAIHeader mode="fullpage" />
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
              <Box sx={{ maxWidth: 600, mx: "auto", width: "100%", pb: 2 }}>
                <SkillPicker onSkillsChanged={loadSkillsAndConnectors} />
              </Box>
            )}
          </Box>
          <ChatInput onSend={handleSend} onStop={sendStop} />
        </Box>
      )}
    </Box>
  );
}
