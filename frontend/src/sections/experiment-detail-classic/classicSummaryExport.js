// The classic Summary's `More` actions (Download…, Print or save as PDF,
// Copy summary) read the table this page shows: the grid's own column defs
// (header names as on screen) and each cell's on-screen text. Pure, apart
// from saveFile (the browser's download).
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import { csvFileName } from "src/sections/common/Analysis/analysisCsv";

const NAME_FIELD = "experiment_dataset_name";

const text = (value) =>
  value === null || value === undefined ? "" : String(value);

/**
 * An eval cell as ExperimentEvalCellRenderer shows it: a numeric eval as
 * NumericCell prints it, any other stats score as a rounded percent. Like
 * the table cell, a score of at most 1 reads as a 0–1 fraction.
 */
export function evalCellText(value, col) {
  if (col?.output_type === OutputTypes.NUMERIC) {
    const n = Number(value);
    if (Number.isNaN(n)) return "-";
    return Number.isInteger(n) ? String(n) : n.toFixed(4);
  }
  if (typeof value === "number")
    return `${Math.round(value <= 1 ? value * 100 : value)}%`;
  if (Array.isArray(value)) return value.map(text).join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return text(value);
}

/** One grid cell's on-screen text (its renderer or valueFormatter). */
export function cellText(column, row) {
  const value = column?.field ? row?.[column.field] : undefined;
  if (column?.col) return evalCellText(value, column.col);
  if (typeof column?.valueFormatter === "function") {
    try {
      return text(column.valueFormatter({ value, data: row, colDef: column }));
    } catch {
      return text(value);
    }
  }
  return text(value);
}

/**
 * The table the Summary shows: `headers` are the grid's header names,
 * `lines` one row of on-screen cell text per run (the grid's order), and
 * `nameIndex` the run name column (-1 when there is none).
 * @param {object[]} columns  the grid's column defs
 * @param {object[]} rows  the stats rows the grid shows
 */
export function summaryTable(columns, rows) {
  const cols = (columns ?? []).filter(Boolean);
  return {
    headers: cols.map((c) => text(c.headerName ?? c.field)),
    lines: (rows ?? []).map((row) => cols.map((c) => cellText(c, row))),
    nameIndex: cols.findIndex((c) => c.field === NAME_FIELD),
  };
}

const esc = (v) => (/[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

/** CSV: the header row, then one line per run. */
export function summaryCsv({ headers, lines }) {
  return [headers, ...lines]
    .map((cells) => cells.map(esc).join(","))
    .join("\n");
}

/** JSON: one object per run, keyed by the on-screen header names. */
export function summaryJson({ headers, lines }) {
  return JSON.stringify(
    lines.map((cells) =>
      Object.fromEntries(headers.map((h, i) => [h, cells[i]])),
    ),
    null,
    2,
  );
}

/** Plain text: the title, then each run's name and its values. */
export function summaryText({ headers, lines, nameIndex }, title) {
  const runs = lines.map((cells, i) => {
    const values = headers
      .map((h, j) => (j === nameIndex ? null : `- ${h}: ${cells[j] || "—"}`))
      .filter(Boolean);
    return [cells[nameIndex] || `Run ${i + 1}`, ...values].join("\n");
  });
  return [title, ...runs].join("\n\n");
}

/** `support-prompts-summary-2026-11-10.csv` (or `.json`). */
export function summaryFileName(name, ext, date = new Date()) {
  return csvFileName(`${name || "experiment"}-summary`, date).replace(
    /\.csv$/,
    `.${ext}`,
  );
}

/** Save `content` as a download named `name`; returns its size in bytes. */
export function saveFile(content, name, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return blob.size;
}
