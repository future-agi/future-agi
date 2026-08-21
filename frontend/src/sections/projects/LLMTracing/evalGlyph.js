const TRACE_GLYPH = {
  code: "T",
  label: "Trace-level eval — one result per trace",
};
const SPAN_GLYPH = {
  code: "S",
  label: "Span-level eval — rolled up across this trace's spans",
};

const GLYPH_BY_ROW_TYPE = {
  traces: TRACE_GLYPH,
  trace: TRACE_GLYPH,
  spans: SPAN_GLYPH,
  span: SPAN_GLYPH,
};

export const getGlyphMeta = (rowType) =>
  GLYPH_BY_ROW_TYPE[String(rowType || "").toLowerCase()] || null;
