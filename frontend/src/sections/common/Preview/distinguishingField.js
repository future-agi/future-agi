// W5-10 (DM-11; W5-SCOPE §7 C7): which field tells rows apart.
//
// A row preview that always shows the first text field shows the same
// system prompt on every row. `chooseField` reads up to PREVIEW_SAMPLE_ROWS
// rows of the view and picks the field with the most distinct normalised
// values (ties go to the earlier column). Fields with one distinct value
// across two or more rows are "the same on every row"; nothing is hidden,
// the caller only says so.
//
//   chooseField(rows, fields, {read})
//     -> {field, distinct: {key: n}, same: [{key, label, value}], sampled}
//   normaliseValue(v)   -> the comparison key of one cell value
//   previewLabel(choice, fields) -> "customer_message · +2 fields"

/** How many rows of the view the choice reads (W5-SCOPE §7 C7). */
export const PREVIEW_SAMPLE_ROWS = 200;

const isPlainObject = (v) =>
  v != null && typeof v === "object" && !Array.isArray(v);

/** A table cell may be `{cell_value, ...}` or the value itself. */
export function cellValue(v) {
  if (isPlainObject(v) && "cell_value" in v) return v.cell_value;
  return v;
}

function stableJson(v) {
  if (Array.isArray(v)) return `[${v.map(stableJson).join(",")}]`;
  if (isPlainObject(v)) {
    return `{${Object.keys(v)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${stableJson(v[k])}`)
      .join(",")}}`;
  }
  return JSON.stringify(v);
}

/**
 * The comparison key of one value: text trimmed, inner whitespace collapsed,
 * case folded; objects and lists by their key-sorted JSON; empty values
 * (missing, "", [], {}) all become "".
 */
export function normaliseValue(raw) {
  const v = cellValue(raw);
  if (v == null) return "";
  if (typeof v === "string") return v.trim().replace(/\s+/g, " ").toLowerCase();
  if (Array.isArray(v)) return v.length ? stableJson(v) : "";
  if (isPlainObject(v)) return Object.keys(v).length ? stableJson(v) : "";
  return String(v);
}

const defaultValueOf = (row, key) => row?.[key];

/**
 * A JSON column's cell as its value: the grid table sends JSON cells as
 * text, so `"[]"` reads as an empty list (the grid's own empty rule), not
 * as the same text on every row (M7). Text that is not JSON stays text.
 */
export function jsonCellValue(raw) {
  const v = cellValue(raw);
  if (typeof v !== "string" || !/^\s*[[{]/.test(v)) return v;
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
}

/**
 * @param {object[]} rows   rows of the view, in view order
 * @param {{key: string, label?: string}[]} fields  candidate fields, in column order
 * @param {{read?: (row, key) => unknown}} [opts]
 */
export function chooseField(rows, fields, opts = {}) {
  const valueOf = Object.hasOwn(opts, "read") ? opts.read : defaultValueOf;
  const sample = (rows ?? []).slice(0, PREVIEW_SAMPLE_ROWS);
  const list = (fields ?? []).filter((f) => f && f.key != null);
  const distinct = {};
  const nonEmpty = {};
  const firstValue = {};
  for (const f of list) {
    const seen = new Set();
    let filled = 0;
    for (const row of sample) {
      const raw = valueOf(row, f.key);
      const key = normaliseValue(raw);
      seen.add(key);
      if (key !== "") {
        filled += 1;
        if (!(f.key in firstValue)) firstValue[f.key] = cellValue(raw);
      }
    }
    distinct[f.key] = seen.size;
    nonEmpty[f.key] = filled;
  }
  // Same on every row: one distinct value over two or more rows. A single
  // row compares with nothing, so no field is "the same" there.
  const same =
    sample.length >= 2
      ? list
          .filter((f) => distinct[f.key] === 1 && nonEmpty[f.key] > 0)
          .map((f) => ({
            key: f.key,
            label: f.label ?? f.key,
            value: firstValue[f.key],
          }))
      : [];
  let field = null;
  let best = 0;
  for (const f of list) {
    if (!nonEmpty[f.key]) continue;
    // Strictly greater: ties keep the earlier column.
    if (distinct[f.key] > best) {
      best = distinct[f.key];
      field = f.key;
    }
  }
  return { field, distinct, same, sampled: sample.length };
}

/** `customer_message · +2 fields` (the other fields count, when any). */
export function previewLabel(choice, fields) {
  if (!choice?.field) return "";
  const f = (fields ?? []).find((x) => x.key === choice.field);
  const others = (fields ?? []).length - 1;
  const name = f?.label ?? choice.field;
  if (others <= 0) return name;
  return `${name} · +${others} field${others === 1 ? "" : "s"}`;
}

// integration C-H6: the results grid's input preview. Its rows carry `inputs`
// ({field: value}); the preview shows the field that differs most across
// the view instead of the first text field.

/** Input field keys of the view's rows, in first-seen (column) order. */
export function inputFields(rows) {
  const seen = new Map();
  for (const row of (rows ?? []).slice(0, PREVIEW_SAMPLE_ROWS)) {
    const inputs = row?.inputs;
    if (!isPlainObject(inputs)) continue;
    Object.keys(inputs).forEach((k) => {
      if (!seen.has(k)) seen.set(k, { key: k, label: k });
    });
  }
  return [...seen.values()];
}

/**
 * Prose: a string a person reads, not a bare link / data URI (media) and
 * not serialised JSON (retrieved chunks, tool payloads).
 */
export function isProse(raw) {
  const v = cellValue(raw);
  if (typeof v !== "string") return false;
  const t = v.trim();
  if (!t) return false;
  if (/^(https?:|data:|s3:|gs:)\S*$/i.test(t)) return false;
  if (/^[[{]/.test(t)) {
    try {
      JSON.parse(t);
      return false;
    } catch {
      return true;
    }
  }
  return true;
}

/**
 * `chooseField` over the view's `inputs`, plus the fields it compared. A row
 * preview is read, so prose fields compete first: when some prose field
 * differs across the rows, the choice is among prose fields (a list of
 * retrieved chunks or an image link with more distinct values does not win
 * over the question); otherwise every field competes.
 */
export function inputsPreviewChoice(rows) {
  const fields = inputFields(rows);
  const read = (row, key) => row?.inputs?.[key];
  const all = chooseField(rows, fields, { read });
  const sample = (rows ?? []).slice(0, PREVIEW_SAMPLE_ROWS);
  const prose = fields.filter((f) => {
    const vals = sample
      .map((r) => read(r, f.key))
      .filter((v) => normaliseValue(v) !== "");
    return vals.length > 0 && vals.every(isProse);
  });
  const differs = prose.filter((f) => (all.distinct[f.key] ?? 0) > 1);
  if (!differs.length) return { ...all, fields };
  const pick = chooseField(rows, prose, { read });
  return { ...all, field: pick.field, fields };
}

/**
 * One row's text for the chosen field, or null when there is no choice or
 * this row leaves that field empty (the caller then shows its usual text).
 */
export function previewFieldText(inputs, field) {
  if (!field || !isPlainObject(inputs)) return null;
  const v = cellValue(inputs[field]);
  if (normaliseValue(v) === "") return null;
  return typeof v === "string" ? v : JSON.stringify(v);
}
