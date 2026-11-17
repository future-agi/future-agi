export const MAX_COMPARE_MODELS = 3;

const normalize = (ids) => [...new Set(ids.filter((id) =>
  typeof id === "string" && id.trim() && !id.includes(","),
))].slice(0, MAX_COMPARE_MODELS);

export function parseCompareQuery(search = "") {
  return normalize(new URLSearchParams(search).getAll("compare").flatMap((value) => value.split(",")));
}

export function compareSearch(search, ids) {
  const query = new URLSearchParams(search);
  const selected = normalize(ids);
  if (selected.length) query.set("compare", selected.join(","));
  else query.delete("compare");
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}

export function toggleCompareId(ids, id) {
  return ids.includes(id) ? ids.filter((value) => value !== id) : normalize([...ids, id]);
}
