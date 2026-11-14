import { getObservationType } from "src/components/traceDetailDrawer/DrawerRightRenderer/getSpanData";

/**
 * Walk the span tree and find the first span whose observation_type is
 * "conversation". Voice calls are traces where one of the spans marks the
 * speech portion of the call.
 */
export function findConversationSpan(entries) {
  if (!Array.isArray(entries)) return null;
  for (const entry of entries) {
    const span = entry?.observation_span || entry?.observationSpan;
    if (getObservationType(span) === "conversation") {
      return { entry, span };
    }
    if (entry?.children?.length) {
      const found = findConversationSpan(entry.children);
      if (found) return found;
    }
  }
  return null;
}

/**
 * True when the shared trace payload contains voice-call data — either as
 * top-level fields or as a conversation-type span in the tree.
 */
export function isVoiceCall(resourceData) {
  if (!resourceData) return false;
  if (resourceData.transcript || resourceData.recordings) return true;
  const spans =
    resourceData.observationSpans ||
    resourceData.observation_spans ||
    resourceData.spans;
  if (!Array.isArray(spans)) return false;
  return !!findConversationSpan(spans);
}

/** "chat" for text simulations, "voice" otherwise. */
export function simulationCallKind(call) {
  return call?.simulation_call_type === "text" ? "chat" : "voice";
}
