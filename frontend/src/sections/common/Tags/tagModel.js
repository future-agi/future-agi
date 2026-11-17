// W5-3 (AG-14, RV-13; W5-SCOPE §7 C1): the pure rules of the one workspace
// tag registry, shared by the mock server (w5Tags.js) and the UI.
//
// A tag is {id, name, color, description, created_by, created_at} plus a live
// `used_on: {rows, experiments, datasets}`. Colours are tokens of one 8-token
// palette, assigned in first-use order and editable.

/** The palette: token -> swatch (light and dark read the same swatch). */
export const TAG_PALETTE = Object.freeze([
  { token: "blue", label: "Blue", hex: "#3B82F6" },
  { token: "green", label: "Green", hex: "#16A34A" },
  { token: "amber", label: "Amber", hex: "#D97706" },
  { token: "violet", label: "Violet", hex: "#7C3AED" },
  { token: "red", label: "Red", hex: "#DC2626" },
  { token: "teal", label: "Teal", hex: "#0D9488" },
  { token: "pink", label: "Pink", hex: "#DB2777" },
  { token: "slate", label: "Slate", hex: "#64748B" },
]);
export const TAG_TOKENS = TAG_PALETTE.map((c) => c.token);
const BY_TOKEN = Object.fromEntries(TAG_PALETTE.map((c) => [c.token, c]));

/** Trim and collapse inner whitespace. */
export const normaliseTagName = (name) =>
  String(name ?? "")
    .trim()
    .replace(/\s+/g, " ");

/** Names compare case-insensitively. */
export const tagKey = (name) => normaliseTagName(name).toLowerCase();

/** The swatch of a token (unknown tokens read as the last palette entry). */
export const tagColor = (token) =>
  (BY_TOKEN[token] ?? TAG_PALETTE[TAG_PALETTE.length - 1]).hex;

/** The colour the next new tag gets: first-use order around the palette. */
export function nextTagColor(existingCount) {
  const n = Math.max(0, Number(existingCount) || 0);
  return TAG_TOKENS[n % TAG_TOKENS.length];
}

/** A reason the name cannot be used, or null. */
export function tagNameError(name, tags, { exceptId = null } = {}) {
  const n = normaliseTagName(name);
  if (!n) return "Give the tag a name.";
  const clash = (tags ?? []).find(
    (t) => t.id !== exceptId && tagKey(t.name) === tagKey(n),
  );
  return clash ? `A tag named “${clash.name}” already exists.` : null;
}

/** Tags whose name contains the query (case-insensitive), registry order. */
export function matchTags(tags, query) {
  const q = tagKey(query);
  const list = tags ?? [];
  return q ? list.filter((t) => tagKey(t.name).includes(q)) : list;
}

/** True when the picker should offer `Create tag “<query>”`. */
export function canCreateTag(tags, query) {
  return Boolean(normaliseTagName(query)) && !tagNameError(query, tags);
}

const count = (n, one, many = `${one}s`) =>
  `${Number(n).toLocaleString()} ${n === 1 ? one : many}`;

/** "3 experiments and 12 rows" (parts with zero left out; "nothing" when none). */
export function usedOnText(usedOn) {
  const u = usedOn ?? {};
  const parts = [];
  if (u.experiments) parts.push(count(u.experiments, "experiment"));
  if (u.datasets) parts.push(count(u.datasets, "dataset"));
  if (u.rows) parts.push(count(u.rows, "row"));
  if (!parts.length) return "nothing";
  if (parts.length === 1) return parts[0];
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** The delete confirmation: `Removes “candidate” from 3 experiments and 12 rows`. */
export function deleteConfirmText(tag) {
  const where = usedOnText(tag?.used_on);
  return where === "nothing"
    ? `Deletes “${tag?.name ?? ""}”. Nothing uses it.`
    : `Removes “${tag?.name ?? ""}” from ${where}`;
}

/** Tag records for ids, in the order given (unknown ids dropped). */
export function tagsByIds(tags, ids) {
  const byId = new Map((tags ?? []).map((t) => [t.id, t]));
  return (ids ?? []).map((id) => byId.get(id)).filter(Boolean);
}

/**
 * The ids after toggling one tag for a set of items: if every item has it,
 * remove it from all; otherwise add it to all.
 * @param {string[][]} idLists  each item's tag ids
 */
export function toggleTagAcross(idLists, tagId) {
  const lists = idLists ?? [];
  const all = lists.length > 0 && lists.every((l) => (l ?? []).includes(tagId));
  return lists.map((l) => {
    const set = new Set(l ?? []);
    if (all) set.delete(tagId);
    else set.add(tagId);
    return [...set];
  });
}
