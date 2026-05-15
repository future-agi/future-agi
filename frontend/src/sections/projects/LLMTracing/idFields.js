// Backend treats these columns as direct equality filters; sending
// `col_type` for them routes through the metric pipeline and matches nothing.
export const ID_ONLY_FIELDS = new Set(["trace_id", "span_id"]);
