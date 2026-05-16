export function buildVoiceCallAnnotationSources({
  traceId,
  rootSpanId,
  sessionId,
  module,
  callExecutionId,
}) {
  if (module === "simulate" && callExecutionId) {
    return [{ sourceType: "call_execution", sourceId: callExecutionId }];
  }
  // A voice call is a trace-level object, but the same call may also have queue
  // items added at the root-span or trace-session level (e.g., when a queue
  // operates on conversation spans, or when a session bulk-add scoops up the
  // call). Send every source we have so the sidebar surfaces every queue this
  // call belongs to instead of silently hiding queues whose items live at a
  // different level.
  const sources = [];
  if (traceId) {
    sources.push({
      sourceType: "trace",
      sourceId: traceId,
      spanNotesSourceId: rootSpanId || undefined,
    });
  }
  if (rootSpanId) {
    sources.push({ sourceType: "observation_span", sourceId: rootSpanId });
  }
  if (sessionId) {
    sources.push({
      sourceType: "trace_session",
      sourceId: sessionId,
      spanNotesSourceId: rootSpanId || undefined,
    });
  }
  return sources;
}

/**
 * Build the source list a trace-detail drawer should send to the annotation
 * sidebar. A trace can live in queues at three levels — trace, root/selected
 * span, or trace_session — and the sidebar must query for all of them so labels
 * from every queue this trace belongs to are visible together.
 *
 * Pre-fix the trace drawer only sent the selected span, which silently dropped
 * any queue whose items were added at trace or session level.
 */
export function buildTraceAnnotationSources({ traceId, spanId, sessionId }) {
  const sources = [];
  if (traceId) {
    sources.push({
      sourceType: "trace",
      sourceId: traceId,
      spanNotesSourceId: spanId || undefined,
    });
  }
  if (spanId) {
    sources.push({ sourceType: "observation_span", sourceId: spanId });
  }
  if (sessionId) {
    sources.push({
      sourceType: "trace_session",
      sourceId: sessionId,
      spanNotesSourceId: spanId || undefined,
    });
  }
  return sources;
}

export function buildVoiceCallScoreSource({
  traceId,
  rootSpanId,
  isSimulate,
  callExecutionId,
}) {
  if (isSimulate && callExecutionId) {
    return { sourceType: "call_execution", sourceId: callExecutionId };
  }
  // Keep span as secondary read-only context only; new call annotations save
  // against the trace so default queues do not receive span items.
  if (traceId) {
    return {
      sourceType: "trace",
      sourceId: traceId,
      secondarySourceType: rootSpanId ? "observation_span" : undefined,
      secondarySourceId: rootSpanId || undefined,
    };
  }
  if (rootSpanId) {
    return { sourceType: "observation_span", sourceId: rootSpanId };
  }
  return { sourceType: "trace", sourceId: "" };
}
