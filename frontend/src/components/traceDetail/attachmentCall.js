import { readSpanAttachments } from "../attachments/attachmentPlacement";

// Bounded walk: a trace drawer never scans an unbounded tree on render.
const MAX_SPANS = 5000;

const isCallRecording = (item) =>
  item.reference.modality === "audio" &&
  item.reference.purpose === "conversation_recording";

/**
 * True when a trace is a call whose recording was captured as an attachment
 * (LiveKit, Pipecat): a parentless conversation root, the root Voice
 * Observability reads, plus an authorized audio conversation recording on any
 * of its spans. Calls without attachments (Vapi, Retell) return false and keep
 * their existing Voice Observability view.
 */
export function isAttachmentCall(tree) {
  if (!Array.isArray(tree)) return false;
  const root = tree.some((entry) => {
    const span = entry?.observation_span;
    return (
      !!span &&
      !span.parent_span_id &&
      String(span.observation_type || "").toLowerCase() === "conversation"
    );
  });
  if (!root) return false;
  const stack = [...tree];
  for (let seen = 0; stack.length && seen < MAX_SPANS; seen += 1) {
    const entry = stack.pop();
    if (readSpanAttachments(entry?.observation_span).some(isCallRecording))
      return true;
    if (Array.isArray(entry?.children)) stack.push(...entry.children);
  }
  return false;
}
