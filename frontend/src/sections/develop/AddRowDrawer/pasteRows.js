// Pasted text -> pending `add_row` cells keyed by column id (IT-R1 G5 P3).
// Reuses the Add-dataset file parser for CSV / JSON / JSONL; TSV (a paste
// from a spreadsheet) splits on tabs. Pure.
import { parseImport } from "src/sections/develop/AddDatasetDrawer/importFile";

const norm = (s) =>
  String(s ?? "")
    .trim()
    .toLowerCase();

function parseTsv(text) {
  const lines = String(text)
    .split(/\r?\n/)
    .filter((l) => l.trim() !== "");
  const columns = (lines[0] ?? "").split("\t").map((h) => h.trim());
  const rows = lines.slice(1).map((l) => l.split("\t"));
  return { columns, rows, errors: [] };
}

/** `csv` | `tsv` | `json` | `jsonl` from the first non-blank characters. */
export function pasteFormat(text) {
  const t = String(text ?? "").trimStart();
  if (t.startsWith("[")) return "json";
  if (t.startsWith("{")) {
    const lines = t.split(/\r?\n/).filter((l) => l.trim());
    return lines.length > 1 && lines.every((l) => l.trim().startsWith("{"))
      ? "jsonl"
      : "json";
  }
  const first = t.split(/\r?\n/)[0] ?? "";
  return first.includes("\t") ? "tsv" : "csv";
}

/**
 * IT-R3 G5 P4: the pasted sheet as `{columns: headers, rows: string[][],
 * error}` (CSV, TSV, a JSON array or JSON lines). Pure.
 */
export function parsePasted(text) {
  if (!String(text ?? "").trim()) return { columns: [], rows: [], error: null };
  const fmt = pasteFormat(text);
  const parsed = fmt === "tsv" ? parseTsv(text) : parseImport(text, fmt);
  if (!parsed.columns.length && parsed.errors?.length)
    return { columns: [], rows: [], error: parsed.errors[0].message };
  return {
    columns: parsed.columns.map((h) => String(h ?? "").trim()),
    rows: parsed.rows.map((r) =>
      Array.isArray(r) ? r : parsed.columns.map((h) => r?.[h]),
    ),
    error: null,
  };
}

/**
 * @returns {{rows: object[], matched: string[], ignored: string[],
 *   error: string|null}} `rows` = `{[columnId]: value}` per pasted row
 */
export function pastedRows(text, columns = []) {
  if (!String(text ?? "").trim())
    return { rows: [], matched: [], ignored: [], error: null };
  const fmt = pasteFormat(text);
  const parsed = fmt === "tsv" ? parseTsv(text) : parseImport(text, fmt);
  if (!parsed.columns.length && parsed.errors?.length)
    return {
      rows: [],
      matched: [],
      ignored: [],
      error: parsed.errors[0].message,
    };
  const byName = new Map(
    (columns ?? []).map((c) => [norm(c.name ?? c.headerName), c]),
  );
  const target = parsed.columns.map((h) => byName.get(norm(h)) ?? null);
  const matched = parsed.columns.filter((_, i) => target[i]);
  const ignored = parsed.columns.filter((h, i) => !target[i] && h);
  const rows = parsed.rows.map((cells) => {
    const out = {};
    target.forEach((col, i) => {
      if (col) out[col.id ?? col.field] = cells[i] ?? "";
    });
    return out;
  });
  return { rows, matched, ignored, error: null };
}
