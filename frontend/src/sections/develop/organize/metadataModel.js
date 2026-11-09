// Metadata editing (W9-SCOPE §5 A1): key = value rows, Edit as YAML, and
// the several-datasets form (set or change keys, remove keys). Pure; the
// rules and sentences are orgRules.js', so the dialog refuses what the
// server refuses with the same words.
import YAML from "yaml";
import {
  METADATA_KEY_RE,
  metadataProblem,
  metadataValueProblem,
  keySentence,
} from "./orgRules";

/** Rows `{key, value}` from a metadata map, in key order. */
export const rowsOf = (metadata) =>
  Object.keys(metadata ?? {})
    .sort()
    .map((key) => ({ key, value: metadata[key] }));

/**
 * A typed value from what the user typed in a row: `2` → 2, `true` →
 * true, anything else stays text. Quoted text stays text (`"2"` → "2").
 */
export function typedValue(text) {
  const s = String(text ?? "");
  const t = s.trim();
  if (/^".*"$/.test(t) || /^'.*'$/.test(t)) return t.slice(1, -1);
  if (t === "true") return true;
  if (t === "false") return false;
  if (t !== "" && /^-?\d+(\.\d+)?$/.test(t) && Number.isFinite(Number(t)))
    return Number(t);
  return s;
}

/** How a value shows in its row's text field. */
export const valueInput = (v) =>
  typeof v === "string" &&
  (v === "true" || v === "false" || /^-?\d+(\.\d+)?$/.test(v.trim()))
    ? `"${v}"`
    : String(v ?? "");

/**
 * A map from rows; the first problem as a sentence (duplicate or invalid
 * key, invalid value), or null. Rows with an empty key and value are
 * ignored.
 */
export function mapFromRows(rows) {
  const out = {};
  for (const r of rows ?? []) {
    const key = String(r?.key ?? "").trim();
    const raw = r?.value;
    if (!key && (raw === "" || raw === undefined || raw === null)) continue;
    if (!METADATA_KEY_RE.test(key))
      return { metadata: null, error: keySentence(key) };
    if (Object.prototype.hasOwnProperty.call(out, key))
      return { metadata: null, error: `“${key}” is listed twice.` };
    const value = typeof raw === "string" ? typedValue(raw) : raw;
    const p = metadataValueProblem(key, value);
    if (p) return { metadata: null, error: p };
    out[key] = value;
  }
  return { metadata: out, error: null };
}

/** YAML text for a metadata map (`domain: billing`). */
export function toYaml(metadata) {
  const keys = Object.keys(metadata ?? {}).sort();
  if (!keys.length) return "";
  const ordered = Object.fromEntries(keys.map((k) => [k, metadata[k]]));
  return YAML.stringify(ordered);
}

/**
 * A metadata map from YAML. A parse error names its line: `Line 2: …`.
 * @returns {{metadata: object|null, error: string|null}}
 */
export function fromYaml(text) {
  const src = String(text ?? "");
  if (!src.trim()) return { metadata: {}, error: null };
  const doc = YAML.parseDocument(src, { prettyErrors: false });
  if (doc.errors.length) {
    const e = doc.errors[0];
    const line = e.linePos?.[0]?.line ?? lineOf(src, e.pos?.[0]);
    const what = String(e.message ?? "")
      .split("\n")[0]
      .replace(/ at line \d+.*$/, "");
    return { metadata: null, error: `Line ${line}: ${what}` };
  }
  const value = doc.toJS();
  if (value === null || value === undefined)
    return { metadata: {}, error: null };
  if (typeof value !== "object" || Array.isArray(value))
    return {
      metadata: null,
      error: "Metadata must be key: value lines, one per key.",
    };
  const problem = metadataProblem(value, {});
  if (problem) {
    const key = Object.keys(value).find((k) => problem.includes(`“${k}”`));
    const line = key !== undefined ? lineOfKey(src, key) : null;
    return {
      metadata: null,
      error: line ? `Line ${line}: ${problem}` : problem,
    };
  }
  return { metadata: value, error: null };
}

function lineOf(src, pos) {
  if (!Number.isFinite(pos)) return 1;
  return src.slice(0, pos).split("\n").length;
}

function lineOfKey(src, key) {
  const lines = src.split("\n");
  const i = lines.findIndex((l) =>
    l.replace(/^["']?/, "").startsWith(`${key}`),
  );
  return i === -1 ? null : i + 1;
}

/**
 * The patch the several-datasets form sends to each dataset: keys set (or
 * changed) plus keys removed (`null`). Other keys of each dataset stay.
 */
export function bulkPatch(setRows, removeKeys) {
  const { metadata, error } = mapFromRows(setRows);
  if (error) return { patch: null, error };
  const patch = { ...metadata };
  for (const k of removeKeys ?? []) {
    if (Object.prototype.hasOwnProperty.call(patch, k))
      return { patch: null, error: `“${k}” is both set and removed.` };
    patch[k] = null;
  }
  if (!Object.keys(patch).length)
    return { patch: null, error: "Set or remove at least one key." };
  return { patch, error: null };
}

/**
 * The single-dataset patch: the new map in full, with the keys the user
 * deleted sent as `null` (the server merges).
 */
export function fullPatch(before, after) {
  const patch = { ...(after ?? {}) };
  Object.keys(before ?? {}).forEach((k) => {
    if (!Object.prototype.hasOwnProperty.call(after ?? {}, k)) patch[k] = null;
  });
  return patch;
}

/** Keys several datasets have, for the Remove keys chips (with counts). */
export function keysAcross(datasets) {
  const counts = new Map();
  (datasets ?? []).forEach((d) =>
    Object.keys(d?.metadata ?? {}).forEach((k) =>
      counts.set(k, (counts.get(k) ?? 0) + 1),
    ),
  );
  return [...counts.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([key, count]) => ({ key, count }));
}

/** `Keys you set are added or replaced on each of the 3 datasets; other keys stay.` */
export const bulkCopy = (n) =>
  `Keys you set are added or replaced on each of the ${n} datasets; other keys stay.`;

/** Chips `domain: billing` and `+n` for the Metadata column. */
export function metadataChips(metadata, max) {
  const rows = rowsOf(metadata);
  const shown = rows.slice(0, max).map((r) => `${r.key}: ${String(r.value)}`);
  return { chips: shown, more: Math.max(0, rows.length - shown.length) };
}
