// W5-1 A1 · the expression pill (W5-SCOPE §5 A1): `{type: "expression",
// expr}` rides `filters=` with the other results pills (seam S1). Pure.

export const EXPRESSION_PILL = "expression";
// The pill shows the start of the expression; the tooltip shows all of it.
export const PILL_TEXT_MAX = 60;

/** `{type: "expression", expr}` (text trimmed). */
export const expressionPill = (expr) => ({
  type: EXPRESSION_PILL,
  expr: String(expr ?? "").trim(),
});

export const isExpressionPill = (pill) =>
  pill?.type === EXPRESSION_PILL && typeof pill.expr === "string";

/** `Expression: #2B.correctness < 0.5 and …` (first 60 characters). */
export function expressionPillLabel(pill) {
  const text = String(pill?.expr ?? "")
    .replace(/\s+/g, " ")
    .trim();
  return `Expression: ${text.length > PILL_TEXT_MAX ? `${text.slice(0, PILL_TEXT_MAX).trimEnd()}…` : text}`;
}

/** Replace `old` with `next` in a pill list (editing a pill keeps its place). */
export function replacePill(pills, old, next, same = (a, b) => a === b) {
  const list = pills ?? [];
  const i = list.findIndex((p) => same(p, old));
  if (i < 0) return [...list, next];
  return [...list.slice(0, i), next, ...list.slice(i + 1)];
}
