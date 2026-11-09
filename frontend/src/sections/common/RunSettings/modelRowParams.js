// W6-1 (A2, `prompt:add-model`): a model row added to a prompt card starts
// from the card's first model row's parameters (temperature, max tokens,
// top_p), so "Add model" compares models, not models and params. The
// prompt version's own params are the fallback when no row has any. Pure.
const KEYS = Object.freeze([
  ["temperature", ["temperature"]],
  ["maxTokens", ["maxTokens", "max_tokens"]],
  ["topP", ["topP", "top_p"]],
]);

const pick = (p, names) => {
  for (const n of names) {
    const v = p?.[n];
    if (v !== undefined && v !== null && v !== "") return v;
  }
  return undefined;
};

/** The params a new row preloads: the first row's, else the version's. */
export function inheritedParams(firstRow, fallback) {
  const out = {};
  KEYS.forEach(([k, names]) => {
    const v = pick(firstRow, names) ?? pick(fallback, names);
    if (v !== undefined) out[k] = v;
  });
  return Object.keys(out).length ? out : fallback ?? null;
}
