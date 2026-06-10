import React, { useCallback, useEffect, useRef } from "react";
import { useParams, useNavigate, Navigate } from "react-router-dom";
import Box from "@mui/material/Box";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
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
import prependConversation from "./helpers/prependConversation";

export default function FalconAIFullPage() {
  const { conversationId: urlConversationId } = useParams();
  const navigate = useNavigate();
  // Falcon AI is not available on self-hosted/OSS. If an OSS user reaches this
  // route directly, redirect them away instead of rendering the "feature not
  // available / upgrade your plan" screen (TH-4698). (The FAB and the dashboard
  // index redirect already keep OSS users off Falcon; this closes the
  // direct-URL gap.)
  const { isOSS } = useDeploymentMode();

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

  const { sendChat, sendStop, sendFeedback, sendReconnect, sendConfirmAction } =
    useFalconSocket();
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
          // Add to the conversations list immediately so title_generated
          // finds a row to update
          prependConversation(newConv, text.slice(0, 50));
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

  // OSS users never see the Falcon page (and thus never the upgrade screen) —
  // bounce them to the default dashboard route. (TH-4698)
  if (isOSS) {
    return <Navigate to="/dashboard/prototype" replace />;
  }

  return (
    <Box
      sx={{
        display: "flex",
        height: "100%",
        overflow: "hidden",
        bgcolor: "background.default",
      }}
    >
      <ChatListPanel sendReconnect={sendReconnect} />

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
              onConfirmAction={sendConfirmAction}
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
