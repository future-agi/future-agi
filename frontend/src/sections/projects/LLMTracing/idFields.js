// Native identifiers use exact membership. Explicit attribute source metadata
// takes precedence when a customer attribute shares one of these names.
export const ID_ONLY_FIELDS = new Set(["trace_id", "span_id", "session"]);
