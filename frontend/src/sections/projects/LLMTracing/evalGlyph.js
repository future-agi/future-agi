// Target type is server-provided eval metadata. Do not infer a glyph from the
// current grid because a trace can contain span-targeted evaluations and vice
// versa; session/unknown targets intentionally render no glyph.
export const getEvalTargetGlyph = (targetType) => {
  if (targetType === "span") return "S";
  if (targetType === "trace") return "T";
  return null;
};
