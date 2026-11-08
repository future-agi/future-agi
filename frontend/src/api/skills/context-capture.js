export const traceRefInput = (value) => ({
  project_id: value.projectId, trace_id: value.traceId, span_id: value.spanId, start_time: value.startTime,
});
export const mapContextTraceRef = (value) => ({
  projectId: value.project_id, traceId: value.trace_id, spanId: value.span_id, startTime: value.start_time,
});
