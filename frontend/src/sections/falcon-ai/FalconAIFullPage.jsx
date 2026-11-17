import React, { useCallback, useEffect, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import Box from "@mui/material/Box";
import { enqueueSnackbar } from "src/components/snackbar";
import useFalconStore from "./store/useFalconStore";
import { captureFalconRequest } from "./store/falconRequestGuard";
import {
  beginFalconSubmission,
  clearFalconSubmission,
} from "./store/falconSubmission";
import useFalconSocket from "./hooks/useFalconSocket";
import useFalconCatalog from "./hooks/useFalconCatalog";
import { useFalconContext } from "./hooks/useFalconContext";
import { createConversation, getConversation } from "./hooks/useFalconAPI";
import FalconAIHeader from "./components/FalconAIHeader";
import MessageList from "./components/MessageList";
import ChatInput from "./components/ChatInput";
import ChatListPanel from "./components/ChatListPanel";
import SkillPicker from "./components/SkillPicker";
import CustomizePanel from "./components/CustomizePanel";

// A chat deleted elsewhere, or kept in another workspace, answers "not found".
const CHAT_UNAVAILABLE =
  "This chat isn't available. It may have been deleted, or it may be in another workspace.";

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

  const messages = useFalconStore((s) => s.messages);
  const showCustomize = useFalconStore((s) => s.showCustomize);
  // A reset (workspace recovery, sign-out) remounts the message box, so text
  // in it, typed or put back after a failed send, never carries over into
  // the next workspace. A draft the reset keeps comes back through
  // pendingPrompt when the new box mounts. The sidebar's box already starts
  // empty, because a reset closes the sidebar.
  const resetGeneration = useFalconStore((s) => s.resetGeneration);

  const { sendChat, sendStop, sendFeedback, sendReconnect } = useFalconSocket();
  const context = useFalconContext();

  const loadSkillsAndConnectors = useFalconCatalog();

  // Sync URL ↔ store (one-way: URL wins on mount, store wins after)
  const initializedRef = useRef(false);

  // On mount: if URL has conversationId, load it into store
  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;

    if (urlConversationId) {
      setCurrentConversation(urlConversationId);
      const isCurrent = captureFalconRequest({ conversation: true });
      getConversation(urlConversationId)
        .then((data) => {
          // A recovery or user navigation may have invalidated this load.
          if (!isCurrent()) return;
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
        .catch((error) => {
          // Not found: the link points to a deleted chat (or one in another
          // workspace). Say so and open a new chat; the effect below drops
          // the dead link, and the next message starts a fresh chat.
          if (!isCurrent() || error?.statusCode !== 404) return;
          enqueueSnackbar(CHAT_UNAVAILABLE, { variant: "error" });
          setCurrentConversation(null);
        });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A reset must also discard the old deep link so a remount cannot reload it.
  useEffect(() => {
    if (
      !currentConversationId &&
      urlConversationId &&
      !useFalconStore.getState().currentConversationId
    ) {
      navigate("/dashboard/falcon-ai", { replace: true });
    }
  }, [currentConversationId, urlConversationId, navigate]);

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
        } catch (error) {
          clearFalconSubmission(submissionId);
          // A workspace reset has already put the draft back and said why.
          if (!isCurrent()) return;
          // Nothing was sent: put the message back in the box to send again.
          setPendingPrompt(text);
          // An upgrade-required answer already showed its own message.
          if (!error?.upgrade_required)
            enqueueSnackbar("Couldn't send your message. Try again.", {
              variant: "error",
            });
          return;
        }
      }

      // Capture attached files before clearing
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
      sendChat(text, convId, context, fileIds, attachedFiles, submissionId);

      // Update URL after sending (won't remount due to optional route param)
      navigate(`/dashboard/falcon-ai/${convId}`, { replace: true });
    },
    [
      currentConversationId,
      context,
      setCurrentConversation,
      setPendingPrompt,
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
      <ChatListPanel onResume={sendReconnect} />

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
          <ChatInput
            key={resetGeneration}
            onSend={handleSend}
            onStop={sendStop}
          />
        </Box>
      )}
    </Box>
  );
}
