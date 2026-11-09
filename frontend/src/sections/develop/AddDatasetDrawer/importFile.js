// File import (DL#1, DL#2): check, parse and pre-map a CSV / JSON / JSONL
// file in the browser before upload. Pure; tested in __tests__.
export const MAX_BYTES = 25 * 1024 * 1024;
export const ACCEPTED = Object.freeze(["csv", "json", "jsonl"]);
export const PREVIEW_ROWS = 20;

export const extOf = (name) =>
  String(name ?? "")
    .toLowerCase()
    .match(/\.([a-z0-9]+)$/)?.[1] ?? "";

/** `1.8 MB`, `812 KB`, `640 B`. */
export function fmtBytes(n) {
  const b = Number(n) || 0;
  if (b >= 1024 * 1024) return `${(b / (1024 * 1024)).toFixed(1)} MB`;
  if (b >= 1024) return `${Math.round(b / 1024)} KB`;
  return `${b} B`;
}

/** A sentence when the file can't be imported, else null. */
export function fileProblem({ name, size }) {
  const ext = extOf(name);
  if (!ACCEPTED.includes(ext))
    return `.${ext || "file"} isn't supported. Use CSV, JSON or JSONL.`;
  if (size > MAX_BYTES)
    return `This file is ${fmtBytes(size)}. The limit is 25 MB. Split it into files under 25 MB.`;
  return null;
}

/** One CSV line → cells (quotes, escaped quotes, commas inside quotes). */
export function splitCsvLine(line) {
  return csvRecords(String(line ?? ""))[0]?.cells ?? [""];
}

/**
 * CSV text → records `[{line, cells}]` (1-based line where the record
 * starts). Quoted cells may hold commas, escaped quotes and line breaks;
 * blank lines are skipped.
 */
export function csvRecords(text) {
  const src = String(text ?? "");
  const out = [];
  let cells = [];
  let cur = "";
  let quoted = false;
  let line = 1;
  let start = 1;
  let any = false;
  const endRecord = () => {
    cells.push(cur);
    if (any || cells.length > 1 || cur.trim() !== "")
      out.push({ line: start, cells });
    cells = [];
    cur = "";
    any = false;
  };
  for (let i = 0; i < src.length; i += 1) {
    const ch = src[i];
    if (quoted) {
      if (ch === '"' && src[i + 1] === '"') {
        cur += '"';
        i += 1;
      } else if (ch === '"') quoted = false;
      else {
        if (ch === "\n") line += 1;
        cur += ch;
      }
    } else if (ch === '"') {
      quoted = true;
      any = true;
    } else if (ch === ",") {
      cells.push(cur);
      cur = "";
      any = true;
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && src[i + 1] === "\n") i += 1;
      endRecord();
      line += 1;
      start = line;
    } else cur += ch;
  }
  if (cur !== "" || cells.length || any) endRecord();
  return out;
}

const cellText = (v) =>
  v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);

const isPlainObject = (v) =>
  v != null && typeof v === "object" && !Array.isArray(v);

/** `{"row": {...}}` → `{...}`: one-key object wrappers unwrap (W3-1). */
export function unwrapRecord(record) {
  let r = record;
  for (let i = 0; i < 3; i += 1) {
    const keys = isPlainObject(r) ? Object.keys(r) : [];
    if (keys.length === 1 && isPlainObject(r[keys[0]])) r = r[keys[0]];
    else break;
  }
  return r;
}

/**
 * Nested objects → `parent.child` keys, at most `depth` levels (W3-1
 * `Flatten nested objects`). Lists stay values. Pure.
 */
export function flattenRecord(record, depth = 3) {
  const out = {};
  const walk = (obj, prefix, level) => {
    Object.entries(obj).forEach(([k, v]) => {
      const key = prefix ? `${prefix}.${k}` : k;
      if (isPlainObject(v) && Object.keys(v).length && level < depth)
        walk(v, key, level + 1);
      else out[key] = v;
    });
  };
  walk(isPlainObject(record) ? unwrapRecord(record) : {}, "", 1);
  return out;
}

/** A text cell holding a JSON object or list → that value, else the text. */
export function readJsonCell(v) {
  if (typeof v !== "string") return v;
  const t = v.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return v;
  try {
    const parsed = JSON.parse(t);
    return parsed != null && typeof parsed === "object" ? parsed : v;
  } catch {
    return v;
  }
}

/**
 * `Column "intent" appears twice (columns 3 and 7). Rename one and upload
 * again.` for the first repeated header, else null (OP-01).
 */
export function duplicateHeaderProblem(columns = []) {
  const seen = new Map();
  for (let i = 0; i < columns.length; i += 1) {
    const name = String(columns[i] ?? "").trim();
    if (seen.has(name))
      return `Column "${name}" appears twice (columns ${seen.get(name) + 1} and ${i + 1}). Rename one and upload again.`;
    seen.set(name, i);
  }
  return null;
}

/**
 * Parse file text. Returns `{columns, rows (arrays of strings), records
 * (objects by column, typed values), errors: [{line, message}] (first 5),
 * rejected: [{line, reason, text}] (every skipped line), total, problem}`.
 * `opts.flatten` unwraps one-key wrappers and flattens nested objects to
 * `parent.child` columns; `opts.parseJson` reads JSON inside text cells.
 * This is the ONE parser: the upload dialogs and the mock (IM1 `file`
 * source) both call it.
 */
export function parseImport(text, ext, opts = {}) {
  const { flatten = false, parseJson = false } = opts ?? {};
  const rejected = [];
  let records = [];
  let header = null;
  const srcLines = String(text ?? "").split(/\r?\n/);
  const lineText = (n) => srcLines[n - 1] ?? "";
  if (ext === "csv") {
    const recs = csvRecords(text);
    header = recs.length ? recs[0].cells.map((h) => h.trim()) : [];
    recs.slice(1).forEach(({ line, cells }) => {
      if (cells.length !== header.length)
        rejected.push({
          line,
          reason: `Expected ${header.length} values, found ${cells.length}`,
          text: lineText(line),
        });
      else
        records.push(
          Object.fromEntries(
            header.map((h, i) => [
              h,
              parseJson ? readJsonCell(cells[i]) : cells[i],
            ]),
          ),
        );
    });
  } else if (ext === "json") {
    try {
      const v = JSON.parse(text);
      records = Array.isArray(v) ? v : Array.isArray(v?.rows) ? v.rows : [v];
    } catch (e) {
      return {
        columns: [],
        rows: [],
        records: [],
        errors: [{ line: 1, message: `Not valid JSON: ${e.message}` }],
        rejected: [
          {
            line: 1,
            reason: `Not valid JSON: ${e.message}`,
            text: lineText(1),
          },
        ],
        total: 0,
        problem: null,
      };
    }
  } else {
    srcLines.forEach((l, i) => {
      if (!l.trim()) return;
      try {
        const v = JSON.parse(l);
        if (isPlainObject(v)) records.push(v);
        else throw new Error("not an object");
      } catch {
        rejected.push({ line: i + 1, reason: "Not a JSON object", text: l });
      }
    });
  }
  records = records.filter(isPlainObject);
  if (ext !== "csv" && parseJson)
    records = records.map((r) =>
      Object.fromEntries(
        Object.entries(r).map(([k, v]) => [k, readJsonCell(v)]),
      ),
    );
  if (flatten) records = records.map((r) => flattenRecord(r));
  let columns = header && !flatten ? header : [];
  if (!header || flatten) {
    columns = [];
    records.forEach((r) =>
      Object.keys(r).forEach((k) => {
        if (!columns.includes(k)) columns.push(k);
      }),
    );
  }
  const rows = records.map((r) => columns.map((c) => cellText(r[c])));
  const errors = rejected
    .slice(0, 5)
    .map((r) => ({ line: r.line, message: r.reason }));
  return {
    columns,
    rows,
    records,
    errors,
    rejected,
    total: rows.length,
    problem: header ? duplicateHeaderProblem(header) : null,
  };
}

/** The first `n` lines of the file with numbers; skipped lines flagged. */
export function rawSourceLines(text, rejected = [], n = 40) {
  const bad = new Set((rejected ?? []).map((r) => r.line));
  return String(text ?? "")
    .split(/\r?\n/)
    .slice(0, n)
    .map((t, i) => ({ no: i + 1, text: t, bad: bad.has(i + 1) }));
}

/** The skipped lines as a downloadable text (`Download the skipped lines`). */
export const rejectedLinesText = (rejected = []) =>
  (rejected ?? []).map((r) => `${r.line}\t${r.reason}\t${r.text}`).join("\n");

// W3-1: one role list for both upload dialogs (create and add rows).
export const ROLE_OPTIONS = Object.freeze([
  { value: "input", label: "Input" },
  { value: "expected", label: "Expected" },
  { value: "metadata", label: "Metadata" },
  { value: "tags", label: "Tags" },
  { value: "id", label: "ID" },
  { value: "split", label: "Split" },
  { value: "ignore", label: "Ignore" },
]);
export const ROLE_VALUES = Object.freeze(ROLE_OPTIONS.map((r) => r.value));

/** Role hints shown under the role picker. */
export const ROLE_HINTS = Object.freeze({
  id: "Rows with the same ID update the row already in the dataset. Inside the file, the last one wins.",
  tags: "Values split on , ; or | and become row tags.",
  split:
    "Rows join the split named in this column. New split names are created when you save the version.",
});

/** Pre-guessed role from a header name. */
export function guessRole(name) {
  const n = String(name ?? "").toLowerCase();
  if (
    /^(input|question|prompt|query|customer_message|message|instruction)s?$|input|question|prompt/.test(
      n,
    )
  )
    return "input";
  if (/expected|answer|reference|label|target|ground_truth|gold/.test(n))
    return "expected";
  if (/^(id|_id|row_id|uuid)$/.test(n)) return "ignore";
  if (/^(split|splits|partition|fold)$/.test(n)) return "split";
  if (/^tags?$/.test(n)) return "tags";
  return "metadata";
}

/**
 * A small CSV in the shape the importer maps by itself: `customer_message`
 * becomes an Input, `expected_reply` an Expected, the rest Metadata.
 */
export const SAMPLE_CSV_NAME = "sample-dataset.csv";
export const SAMPLE_CSV = [
  "customer_message,expected_reply,intent,language",
  '"Where is my order A-1042? It was due Monday.","Sorry for the wait. Order A-1042 left our warehouse on Friday and arrives Thursday. Here is the tracking link.",order_status,en',
  '"I was charged twice for order A-2213.","I\'m sorry about that. I\'ve refunded the duplicate charge on A-2213; it shows on your card in 3-5 business days.",billing,en',
  '"Can I return shoes I bought 40 days ago?","Our return window is 30 days, so these can\'t be returned, but they are under warranty for defects for 1 year.",returns,en',
].join("\n");

/**
 * Why Import is off, as the button's tooltip; null when it is on.
 * @param {{name?: string, hasFile: boolean, problem?: string|null,
 *   parsed?: object|null, keptColumns?: number, busy?: boolean}} s
 */
export function importBlockReason({
  name,
  hasFile,
  problem,
  parsed,
  keptColumns = 0,
  busy = false,
}) {
  if (busy) return "Creating the dataset…";
  if (!hasFile) return "Choose a file first";
  if (problem) return "Fix the file problem shown above first";
  if (!parsed) return "Reading the file…";
  if (!String(name ?? "").trim()) return "Name the dataset first";
  if (parsed.problem) return parsed.problem;
  if (!keptColumns)
    return "Keep at least one column (set a role other than Ignore)";
  return null;
}
