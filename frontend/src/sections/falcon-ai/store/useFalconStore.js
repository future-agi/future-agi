import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";

let _blockIdCounter = 0;

/**
 * Build an ordered blocks array from legacy content + tool_calls fields.
 * Used when loading historical messages that don't have blocks yet.
 */
function _buildBlocksFromLegacy(msg) {
  const blocks = [];
  // For historical messages, show tool calls first then text (original order)
  if (msg.tool_calls?.length) {
    msg.tool_calls.forEach((tc) => {
      blocks.push({
        type: "tool_call",
        id: tc.call_id || `tc-${blocks.length}`,
        toolCall: tc,
      });
    });
  }
  if (msg.content) {
    blocks.push({ type: "text", id: "text-main", content: msg.content });
  }
  if (msg.completion_card) {
    blocks.push({
      type: "completion_card",
      id: "completion",
      card: msg.completion_card,
    });
  }
  return blocks;
}

const useFalconStore = create((set, _get) => ({
  // Sidebar state
  isSidebarOpen: false,
  openSidebar: () => set({ isSidebarOpen: true }),
  closeSidebar: () => set({ isSidebarOpen: false }),
  toggleSidebar: () => set((s) => ({ isSidebarOpen: !s.isSidebarOpen })),

  // Context selector
  selectedContext: "auto",
  setSelectedContext: (ctx) => set({ selectedContext: ctx }),
  activePageContext: null,
  setActivePageContext: (context) => set({ activePageContext: context }),
  clearActivePageContext: (sourceId = null) =>
    set((s) => {
      if (sourceId && s.activePageContext?.source_id !== sourceId) return {};
      return { activePageContext: null };
    }),

  // Skills
  skills: [],
  activeSkill: null,
  setSkills: (skills) => set({ skills }),
  setActiveSkill: (skill) => set({ activeSkill: skill }),
  clearActiveSkill: () => set({ activeSkill: null }),

  // Attached files for current message
  attachedFiles: [], // [{id, name, size, content_type, url}]
  addAttachedFile: (file) =>
    set((s) => ({ attachedFiles: [...s.attachedFiles, file] })),
  removeAttachedFile: (id) =>
    set((s) => ({
      attachedFiles: s.attachedFiles.filter((f) => f.id !== id),
    })),
  clearAttachedFiles: () => set({ attachedFiles: [] }),

  // Connectors (cached for display in context selector)
  connectors: [],
  setConnectors: (connectors) => set({ connectors }),

  // Skills menu trigger (for slash command /skills)
  skillsMenuTrigger: 0,
  triggerSkillsMenu: () =>
    set((s) => ({ skillsMenuTrigger: s.skillsMenuTrigger + 1 })),

  // Customize panel
  showCustomize: false,
  setShowCustomize: (show) => set({ showCustomize: show }),

  // Pending prefill — set by callers (e.g. "Fix with Falcon" buttons) that open
  // the sidebar with a pre-composed message. ChatInput hydrates its text from
  // this on mount and clears it. Skills are activated via inline /<slug>
  // syntax within the prompt itself, so no separate skill field is needed.
  pendingPrompt: null,
  setPendingPrompt: (prompt) => set({ pendingPrompt: prompt }),
  clearPendingPrompt: () => set({ pendingPrompt: null }),

  // Chat state
  conversations: [],
  currentConversationId: null,
  messages: [],
  isStreaming: false,
  streamingMessageId: null,

  // Conversation actions
  setConversations: (conversations) => set({ conversations }),
  setCurrentConversation: (id) =>
    set({ currentConversationId: id, messages: [] }),

  // Message actions
  setMessages: (messages) =>
    set({
      messages: messages.map((m) => ({
        ...m,
        blocks: m.blocks?.length ? m.blocks : _buildBlocksFromLegacy(m),
      })),
    }),
  addMessage: (message) =>
    set((s) => ({
      messages: [...s.messages, { ...message, blocks: message.blocks || [] }],
    })),
  updateMessage: (id, updates) =>
    set((s) => ({
      messages: s.messages.map((m) => {
        if (m.id !== id) return m;
        const updated = { ...m, ...updates };
        // If a completion_card was just set, add it as a block
        if (updates.completion_card && updated.blocks?.length) {
          // Only add if not already present
          const hasCompletionBlock = updated.blocks.some(
            (b) => b.type === "completion_card",
          );
          if (!hasCompletionBlock) {
            updated.blocks = [
              ...updated.blocks,
              {
                type: "completion_card",
                id: "completion",
                card: updates.completion_card,
              },
            ];
          }
        }
        // If blocks not set, build from content + tool_calls (backward compat)
        if (
          !updated.blocks &&
          (updated.content || updated.tool_calls?.length)
        ) {
          updated.blocks = _buildBlocksFromLegacy(updated);
        }
        return updated;
      }),
    })),
  appendTextDelta: (messageId, delta) =>
    set((s) => ({
      messages: s.messages.map((m) => {
        if (m.id !== messageId) return m;
        const blocks = m.blocks || [];
        const lastBlock = blocks[blocks.length - 1];
        // If last block is text, append to it
        if (lastBlock && lastBlock.type === "text") {
          return {
            ...m,
            content: (m.content || "") + delta,
            blocks: [
              ...blocks.slice(0, -1),
              { ...lastBlock, content: lastBlock.content + delta },
            ],
          };
        }
        // Otherwise create new text block
        return {
          ...m,
          content: (m.content || "") + delta,
          blocks: [
            ...blocks,
            { type: "text", id: `text-${++_blockIdCounter}`, content: delta },
          ],
        };
      }),
    })),

  // Tool call actions (stored on the message object)
  addToolCall: (messageId, toolCall) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === messageId
          ? {
              ...m,
              tool_calls: [...(m.tool_calls || []), toolCall],
              blocks: [
                ...(m.blocks || []),
                { type: "tool_call", id: toolCall.call_id, toolCall },
              ],
            }
          : m,
      ),
    })),
  updateToolCall: (messageId, callId, updates) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === messageId
          ? {
              ...m,
              tool_calls: (m.tool_calls || []).map((tc) =>
                tc.call_id === callId ? { ...tc, ...updates } : tc,
              ),
              blocks: (m.blocks || []).map((b) =>
                b.type === "tool_call" && b.id === callId
                  ? { ...b, toolCall: { ...b.toolCall, ...updates } }
                  : b,
              ),
            }
          : m,
      ),
    })),

  // Streaming
  setStreaming: (isStreaming, messageId = null) =>
    set({ isStreaming, streamingMessageId: messageId }),

  // Navigation (from agent navigate events)
  pendingNavigation: null,
  setPendingNavigation: (path) => set({ pendingNavigation: path }),
  clearPendingNavigation: () => set({ pendingNavigation: null }),

  // Reset
  resetChat: () =>
    set({
      messages: [],
      isStreaming: false,
      streamingMessageId: null,
      attachedFiles: [],
    }),
  resetAll: () =>
    set({
      isSidebarOpen: false,
      conversations: [],
      currentConversationId: null,
      messages: [],
      isStreaming: false,
      streamingMessageId: null,
      selectedContext: "auto",
      activePageContext: null,
      activeSkill: null,
      attachedFiles: [],
      pendingNavigation: null,
      showCustomize: false,
      pendingPrompt: null,
    }),
}));

export default useFalconStore;
export const useShallowFalcon = (fn) => useFalconStore(useShallow(fn));
