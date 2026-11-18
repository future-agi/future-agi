/**
 * Builder chat focus: another part of the workspace asks the builder chat to open at one of its messages (a question
 * or confirmation waiting for the person). The chat pane opens; the console scrolls to that message.
 */

const listeners = new Set();

export function subscribeBuilderChatFocus(fn) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function focusBuilderChat(messageId) {
  if (!messageId) return;
  listeners.forEach((fn) => fn(messageId));
}
