// Inferred schema (R2-SPEC P6 §3.3 / §3.4): typed paths with kind mix,
// coverage, an example and value ranges, from sampled cell values. Used by
// the mock json-schema (`withTypedPaths`) and by the Schema drawer for the
// top-level columns. Pure; never throws.
import { formatPath } from "./faPath";
import { jsonKind, sniffValue } from "./sniff";

const MAX_EXAMPLE = 80;
const ENUM_MAX = 8;

const parseJsonText = (v) => {
  if (typeof v !== "string") return v;
  const t = v.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return v;
  try {
    return JSON.parse(t);
  } catch {
    return v;
  }
};

/** Short display text of an example value. */
export function exampleText(v) {
  if (v === undefined) return "";
  let s;
  if (typeof v === "string") s = JSON.stringify(v);
  else {
    try {
      s = JSON.stringify(v);
    } catch {
      s = String(v);
    }
  }
  if (s === undefined) s = String(v);
  return s.length > MAX_EXAMPLE ? `${s.slice(0, MAX_EXAMPLE - 1)}…` : s;
}

/**
 * Typed paths of one JSON column.
 * @param {Array<*>} values  one stored value per sampled row (JSON text or
 *   parsed; undefined / null when the row has none)
 * @param {{column?: string, maxDepth?: number, maxPaths?: number,
 *   edgeIndexes?: boolean}} [opts]  `column` prefixes every path;
 *   `edgeIndexes` adds `[0]` / `[-1]` variants under the first array level;
 *   `rowIds` (one per value) adds `missing_row_ids` (≤ 100) to sparse paths
 * @returns {Array<{path: string, rel: string, kinds: Object<string, number>,
 *   counts: Object<string, number>, coverage: {present: number, total: number},
 *   example: *, enum_values?: string[], min?: number, max?: number,
 *   array_max?: number, max_length?: number}>}
 */
export function inferPaths(
  values,
  {
    column = null,
    maxDepth = 6,
    maxPaths = 200,
    edgeIndexes = true,
    rowIds = null,
  } = {},
) {
  const total = values.length;
  const byPath = new Map();
  const order = [];

  const entryFor = (steps) => {
    const rel = formatPath(null, steps);
    let e = byPath.get(rel);
    if (!e) {
      if (byPath.size >= maxPaths) return null;
      e = {
        rel,
        steps,
        rows: new Set(),
        kindRows: {},
        example: undefined,
        strings: new Map(),
        min: Infinity,
        max: -Infinity,
        arrayMax: 0,
        maxLength: 0,
      };
      byPath.set(rel, e);
      order.push(rel);
    }
    return e;
  };

  const record = (steps, v, rowIdx) => {
    if (!steps.length) return;
    const e = entryFor(steps);
    if (!e) return;
    const kind = jsonKind(v);
    if (kind === "null") return;
    if (!e.rows.has(rowIdx)) {
      e.rows.add(rowIdx);
      e.kindRows[kind] = (e.kindRows[kind] ?? 0) + 1;
    }
    if (e.example === undefined && kind !== "object" && kind !== "array")
      e.example = v;
    if (kind === "string") {
      e.strings.set(v, (e.strings.get(v) ?? 0) + 1);
      e.maxLength = Math.max(e.maxLength, v.length);
    } else if (kind === "number") {
      e.min = Math.min(e.min, v);
      e.max = Math.max(e.max, v);
    } else if (kind === "array") {
      e.arrayMax = Math.max(e.arrayMax, v.length);
    }
  };

  const walk = (raw, steps, depth, rowIdx, indexed) => {
    const v = parseJsonText(raw);
    record(steps, v, rowIdx);
    if (depth >= maxDepth || v === null || typeof v !== "object") return;
    if (Array.isArray(v)) {
      // numeric arrays (embeddings) list one `[*]` path, no per-item walk
      if (v.length > 64 && v.every((x) => typeof x === "number")) {
        v.slice(0, 64).forEach((item) =>
          record([...steps, { type: "wildcard" }], item, rowIdx),
        );
        return;
      }
      v.forEach((item) =>
        walk(item, [...steps, { type: "wildcard" }], depth + 1, rowIdx, true),
      );
      if (edgeIndexes && !indexed && v.length) {
        walk(
          v[0],
          [...steps, { type: "index", index: 0 }],
          depth + 1,
          rowIdx,
          true,
        );
        walk(
          v[v.length - 1],
          [...steps, { type: "index", index: -1 }],
          depth + 1,
          rowIdx,
          true,
        );
      }
      return;
    }
    Object.entries(v).forEach(([k, item]) =>
      walk(
        item,
        [...steps, { type: "key", key: k }],
        depth + 1,
        rowIdx,
        indexed,
      ),
    );
  };

  values.forEach((raw, rowIdx) => {
    const v = parseJsonText(raw);
    if (v === null || v === undefined || typeof v !== "object") return;
    walk(v, [], 0, rowIdx, false);
  });

  return order.map((rel) => {
    const e = byPath.get(rel);
    const present = e.rows.size;
    const kinds = {};
    Object.entries(e.kindRows).forEach(([k, n]) => {
      kinds[k] = total ? n / total : 0;
    });
    if (total > present) kinds.null = (total - present) / total;
    const out = {
      path: column ? formatPath(column, e.steps) : rel,
      rel,
      kinds,
      counts: {
        ...e.kindRows,
        ...(total > present ? { null: total - present } : {}),
      },
      coverage: { present, total },
      example: e.example === undefined ? null : e.example,
    };
    const distinct = e.strings.size;
    if (
      distinct > 0 &&
      distinct <= ENUM_MAX &&
      present >= distinct * 3 &&
      Object.keys(e.kindRows).length === 1
    )
      out.enum_values = [...e.strings.entries()]
        .sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])))
        .map(([s]) => s);
    if (e.min !== Infinity) {
      out.min = e.min;
      out.max = e.max;
    }
    if (e.arrayMax) out.array_max = e.arrayMax;
    if (Array.isArray(rowIds) && present < total && total - present <= 100)
      out.missing_row_ids = rowIds.filter((_, i) => !e.rows.has(i));
    if (e.maxLength) out.max_length = e.maxLength;
    return out;
  });
}

/**
 * Kind of a top-level cell for the Schema drawer (string/number/…/null),
 * from its sniffed value. integration review 2 (RV-02): an object read as chat
 * because it holds a `turns` / `messages` list (`meta.wrapped`) is stored
 * as an object, so its schema type is `object` (its other keys and nested
 * paths stay inferred); only a bare message list is an `array`.
 */
function kindOfSniffed(s) {
  switch (s.kind) {
    case "empty":
      return "null";
    case "number":
      return "number";
    case "boolean":
      return "boolean";
    case "object":
    case "ref":
      return "object";
    case "chat":
      return s.meta?.wrapped ? "object" : "array";
    case "array":
    case "images":
    case "embedding":
      return "array";
    default:
      return "string";
  }
}

/**
 * One entry per column (the top-level path), Schema-drawer shaped like
 * inferPaths entries, plus `type` (declared) and `json` (has nested paths).
 * @param {{id: string, name: string, data_type?: string, dataType?: string}[]} columns
 * @param {Array<Object<string, *>>} rows  values keyed by column id
 */
export function inferColumns(columns, rows) {
  const total = rows.length;
  return columns.map((c) => {
    const declaredType = c.data_type ?? c.dataType;
    const counts = {};
    const strings = new Map();
    let min = Infinity;
    let max = -Infinity;
    let example;
    let maxLength = 0;
    rows.forEach((r) => {
      const raw = r?.[c.id];
      const s = sniffValue(raw, { declaredType });
      const kind = kindOfSniffed(s);
      counts[kind] = (counts[kind] ?? 0) + 1;
      if (kind === "null") return;
      // A wrapped conversation's example is the stored object, not the
      // normalised messages the Chat view reads.
      if (example === undefined)
        example = s.meta?.wrapped ? parseJsonText(raw) : s.normalized;
      if (kind === "number") {
        min = Math.min(min, s.normalized);
        max = Math.max(max, s.normalized);
      }
      if (kind === "string" && typeof raw === "string") {
        strings.set(raw, (strings.get(raw) ?? 0) + 1);
        maxLength = Math.max(maxLength, raw.length);
      }
    });
    const present = total - (counts.null ?? 0);
    const kinds = Object.fromEntries(
      Object.entries(counts).map(([k, n]) => [k, total ? n / total : 0]),
    );
    const out = {
      path: c.name,
      rel: "",
      column_id: c.id,
      type: declaredType,
      kinds,
      counts,
      coverage: { present, total },
      example: example === undefined ? null : example,
    };
    if (
      strings.size > 0 &&
      strings.size <= ENUM_MAX &&
      present >= strings.size * 3 &&
      Object.keys(counts).filter((k) => k !== "null").length === 1 &&
      maxLength <= 40
    )
      out.enum_values = [...strings.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([s]) => s);
    if (min !== Infinity) {
      out.min = min;
      out.max = max;
    }
    if (maxLength) out.max_length = maxLength;
    return out;
  });
}

/** `34 of 40` / `all 40`. */
export const coverageText = ({ present, total } = {}) =>
  total && present === total
    ? `all ${total.toLocaleString("en-US")}`
    : `${(present ?? 0).toLocaleString("en-US")} of ${(total ?? 0).toLocaleString("en-US")}`;

/** `85%` / `all rows` (PathInput options). */
export const coveragePercent = ({ present, total } = {}) => {
  if (!total) return "";
  if (present === total) return "all rows";
  const pct = (present / total) * 100;
  return `${Number.isInteger(pct) ? pct : pct.toFixed(1)}%`;
};

/** `number 95% · string 5%` (kinds mix, largest first, null last). */
export function kindsMixText(kinds = {}) {
  return Object.entries(kinds)
    .filter(([, f]) => f > 0)
    .sort((a, b) => (a[0] === "null" ? 1 : b[0] === "null" ? -1 : b[1] - a[1]))
    .map(([k, f]) => {
      const pct = f * 100;
      return `${k} ${Number.isInteger(pct) ? pct : pct.toFixed(1)}%`;
    })
    .join(" · ");
}

/** Dominant kind of an inferred entry (ignoring null). */
export function dominantKind(kinds = {}) {
  const best = Object.entries(kinds)
    .filter(([k]) => k !== "null")
    .sort((a, b) => b[1] - a[1])[0];
  return best ? best[0] : "null";
}
