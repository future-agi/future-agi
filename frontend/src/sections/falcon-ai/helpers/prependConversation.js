import useFalconStore from "../store/useFalconStore";

/**
 * Prepend a freshly created conversation to the conversations list so the
 * later `title_generated` event has a row to map-update. Shared by both
 * shells' handleSend — sidebar-born chats previously skipped this and lost
 * their generated titles.
 */
export default function prependConversation(conv, fallbackTitle) {
  const store = useFalconStore.getState();
  store.setConversations([
    {
      id: conv.id,
      title: conv.title || fallbackTitle,
      created_at: conv.created_at || new Date().toISOString(),
    },
    ...(store.conversations || []),
  ]);
}
