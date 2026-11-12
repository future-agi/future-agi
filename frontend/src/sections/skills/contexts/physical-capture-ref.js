// Carry the selected physical span, never reconstruct it from a trace summary.
export function physicalCaptureRef(projectId, span) {
  const ref = { projectId, traceId: span?.trace, spanId: span?.id, startTime: span?.start_time };
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(projectId || '') ||
    ![ref.traceId, ref.spanId].every((value) => typeof value === 'string' && value.length > 0 && value.length <= 200 && [...value].every((char) => char.charCodeAt(0) >= 32)) ||
    typeof ref.startTime !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/.test(ref.startTime) || !Number.isFinite(Date.parse(ref.startTime))) return null;
  // Preserve fractional precision; converting through Date loses microseconds.
  return ref;
}
// The context a span was delivered from, as exported by the Hub SDK. Only a default for the
// destination choice: capture is still authorized by the server for the chosen context.
export function contextHintFrom(span) {
  const value = span?.span_attributes?.['fi.context.id'];
  return typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value) ? value : undefined;
}
