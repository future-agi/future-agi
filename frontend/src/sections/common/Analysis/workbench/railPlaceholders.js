// L7c g6 (ex:group-by): the Path and Expression placeholders name a column
// of THIS dataset (never another dataset's field). Pure.
const nameOf = (c) => (typeof c === "string" ? c : c?.name ?? c?.label ?? null);

export function railPlaceholders(columns = []) {
  const names = (columns ?? []).map(nameOf).filter(Boolean);
  const first = names[0];
  return {
    path: first ? `e.g. ${first}.field or ${first}[0]` : "e.g. column.field[0]",
    expression: first ? `e.g. len(${first}) > 400` : "e.g. len(column) > 400",
  };
}
