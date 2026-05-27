import React, { useCallback, useEffect, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import Box from "@mui/material/Box";
import { useAuthContext } from "src/auth/hooks";
import {
  buildCurrentFlowContext,
  CurrentFlowEvents,
  trackCurrentFlow,
} from "src/utils/analytics/currentFlow";
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
  const { user } = useAuthContext();

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

  useEffect(() => {
    if (!user) return;
    trackCurrentFlow(
      CurrentFlowEvents.currentFlowFalconViewed,
      {
        ...buildCurrentFlowContext({ user }),
        event_source: "falcon_full_page",
        has_conversation_id: Boolean(urlConversationId),
      },
      {
        onceKeyParts: ["falconViewed", user?.default_workspace_id, user?.id],
      },
    );
  }, [urlConversationId, user]);

  // Sync URL ↔ store (one-way: URL wins on mount, store wins after)
  const initializedRef = useRef(false);

  // On mount: if URL has conversationId, load it into store
  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;

    if (urlConversationId) {
      setCurrentConversation(urlConversationId);
      getConversation(urlConversationId)
        .then((data) => {
          const conv = data.result || data;
          // Normalize camelCase API response to snake_case for components
          const messages = (conv.messages || []).map((m) => ({
            ...m,
            tool_calls: m.tool_calls || [],
            completion_card: m.completion_card || null,
            created_at: m.created_at,
          }));
          useFalconStore.getState().setMessages(messages);
        })
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
      const contextProperties = buildCurrentFlowContext({ user });

      trackCurrentFlow(
        CurrentFlowEvents.currentFlowFalconMessageSent,
        {
          ...contextProperties,
          event_source: "falcon_full_page",
          conversation_id: convId,
          had_existing_conversation: Boolean(currentConversationId),
          attached_file_count: fileIds.length,
        },
        {
          onceKeyParts: [
            "falconMessageSent",
            user?.default_workspace_id,
            user?.id,
          ],
        },
      );
      trackCurrentFlow(
        CurrentFlowEvents.currentFlowFirstValueCandidate,
        {
          ...contextProperties,
          event_source: "falcon_full_page",
          candidate_type: "falcon_first_message",
          conversation_id: convId,
        },
        {
          onceKeyParts: [
            "firstValueCandidate",
            user?.default_workspace_id,
            user?.id,
          ],
        },
      );

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
      user,
      setCurrentConversation,
      addMessage,
      setStreaming,
      sendChat,
      navigate,
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
