// TH-150: per-editor callback registry for React-backed Quill embeds.
//
// Functions cannot survive a blot's value() round-trip (history replay, paste),
// so the blots resolve their delete/magnify/replace/edit callbacks from the
// owning Quill instance at attach() time instead of trusting the insert value.
import Quill from "quill";

const REGISTRY = new WeakMap(); // Quill instance -> callbacks object

export function setEmbedCallbacks(quill, callbacks) {
  if (quill) REGISTRY.set(quill, callbacks || {});
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
