/**
 * Composer scaffold — suggestion chips pinned inside the builder
 * chat's input area.
 *
 * A caller (the SelectionBar, or any future surface with a
 * suggestion strip) emits a scaffold; the AssistantConsole
 * subscribes and pins the text as a removable chip above its
 * text field, exactly the way Falcon's ChatInput renders a
 * clicked skill. On send, every pinned scaffold's text is
 * prepended to the message the user typed and the pins clear.
 */

const listeners = new Set();

export function subscribeComposerScaffold(fn) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export function injectComposerScaffold(text) {
  const clean = (text || "").trim();
  if (!clean) return;
  listeners.forEach((fn) => {
    try { fn(clean); } catch { /* no-op */ }
  });
}
