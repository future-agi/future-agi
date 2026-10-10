// Functions cannot survive a blot's value() round trip (history replay, paste),
// so React-backed embeds look their callbacks up from the owning Quill
// instance at attach() time.
import Quill from "quill";

const REGISTRY = new WeakMap(); // Quill instance -> callbacks object

// Undefined entries are dropped so they cannot shadow a callback carried in
// an inserted value (the blots fall back to those).
export function setEmbedCallbacks(quill, callbacks) {
  if (!quill) return;
  const defined = Object.fromEntries(
    Object.entries(callbacks || {}).filter(([, value]) => value !== undefined),
  );
  REGISTRY.set(quill, defined);
}

export function getEmbedCallbacksForQuill(quill) {
  return (quill && REGISTRY.get(quill)) || {};
}

/** Finds the Quill instance that owns a blot (null while the blot is detached). */
export function resolveQuill(blot) {
  const editorNode = blot?.scroll?.domNode; // .ql-editor
  const container = editorNode?.parentNode; // .ql-container, Quill registers the instance here
  if (!container) return null;
  const found = Quill.find(container);
  return found instanceof Quill ? found : null;
}

export function getEmbedCallbacks(blot) {
  return getEmbedCallbacksForQuill(resolveQuill(blot));
}
