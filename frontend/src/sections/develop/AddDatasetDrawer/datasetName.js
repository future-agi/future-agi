// Live dataset-name validation (DL#7). Pure; tested.
export const NAME_MAX = 80;
export const NAME_COUNTER_FROM = 60;

/**
 * @param {string} name
 * @param {{name: string, id?: string}[]} existing  loaded datasets
 * @param {{ignoreId?: string}} [opts]  the dataset being renamed
 * @returns {{ok: boolean, error: string|null, clash: {id, name}|null,
 *   counter: string|null}}
 */
export function validateDatasetName(name, existing = [], { ignoreId } = {}) {
  const v = String(name ?? "");
  const trimmed = v.trim();
  const counter =
    v.length >= NAME_COUNTER_FROM ? `${v.length}/${NAME_MAX}` : null;
  if (!trimmed) return { ok: false, error: null, clash: null, counter };
  if (v.length > NAME_MAX)
    return {
      ok: false,
      error: `Keep it under ${NAME_MAX} characters.`,
      clash: null,
      counter,
    };
  const clash = (existing ?? []).find(
    (d) =>
      d?.id !== ignoreId &&
      String(d?.name ?? "")
        .trim()
        .toLowerCase() === trimmed.toLowerCase(),
  );
  if (clash)
    return {
      ok: false,
      error: `A dataset named "${clash.name}" already exists.`,
      clash,
      counter,
    };
  return { ok: true, error: null, clash: null, counter };
}

/** Column names for a new blank dataset: trimmed, unique, non-empty. */
export function cleanColumns(columns) {
  const seen = new Set();
  return (columns ?? [])
    .map((c) => ({ ...c, name: String(c?.name ?? "").trim() }))
    .filter((c) => {
      if (!c.name || seen.has(c.name.toLowerCase())) return false;
      seen.add(c.name.toLowerCase());
      return true;
    });
}

export const DEFAULT_NEW_COLUMNS = Object.freeze([
  { name: "input", role: "input" },
  { name: "expected_output", role: "expected" },
]);
