const OPENTELEMETRY_TRACE_ID = /^[0-9a-f]{32}$/i;
const DASHED_TRACE_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * The span store's form of a trace id: a lowercase dashed UUID string.
 *
 * Experiment runs record OpenTelemetry's 32 hex digits; the span store keeps
 * the same 128 bits as a dashed UUID string, the form every trace view, link
 * and annotation uses. Either form, in any case, gives that string. Any other
 * value is given as it is. The server applies the same rule
 * (tracer/utils/trace_ids.py).
 */
export function toStoredTraceId(value) {
  if (typeof value !== "string") return value;
  const text = value.trim().toLowerCase();
  if (OPENTELEMETRY_TRACE_ID.test(text)) {
    return [
      text.slice(0, 8),
      text.slice(8, 12),
      text.slice(12, 16),
      text.slice(16, 20),
      text.slice(20),
    ].join("-");
  }
  if (DASHED_TRACE_ID.test(text)) return text;
  return value;
}
