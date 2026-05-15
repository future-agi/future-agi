export function buildVoiceCallAnnotationSources({
  traceId,
  rootSpanId,
  module,
  callExecutionId,
}) {
  if (module === "simulate" && callExecutionId) {
    return [{ sourceType: "call_execution", sourceId: callExecutionId }];
  }
  // A voice call is a trace-level object. The root conversation span is useful
  // for analytics and item notes, but direct call annotation must create trace
  // annotations.
  if (traceId) {
    return [
      {
        sourceType: "trace",
        sourceId: traceId,
        spanNotesSourceId: rootSpanId || undefined,
      },
    ];
  }
  if (rootSpanId) {
    return [{ sourceType: "observation_span", sourceId: rootSpanId }];
  }
  return [];
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
