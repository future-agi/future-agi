// Share the Workbench (R2-SPEC F11 part): `Copy as Markdown` and
// `Export CSV`. Pure: the caller passes what is on screen.
import { fmtValue } from "./workbenchFormat";

const cell = (v) =>
  String(v ?? "")
    .replace(/\|/g, "\\|")
    .replace(/\r?\n/g, " ");

/**
 * The visible pivot as plain table data (formatted like the screen).
 * @returns {{head: string[], rows: string[][]}}
 */
export function pivotTableData(
  pivot,
  groups,
  measure,
  { groupTitle = "Group" } = {},
) {
  if (!pivot) return { head: [], rows: [] };
  const cols = pivot.columns ?? [];
  const fmt = (c) =>
    pivot.failedEval ? String(c?.n ?? 0) : fmtValue(measure, c?.value);
  return {
    head: [groupTitle, ...cols.map((c) => c.label), "Total"],
    rows: [
      ...(groups ?? []).map((g) => [
        `${g} (${pivot.rowTotals[g]?.rows ?? 0})`,
        ...cols.map((c) => fmt(pivot.cells[g]?.[c.key])),
        fmt(pivot.rowTotals[g]),
      ]),
      [
        "Total",
        ...cols.map((c) => fmt(pivot.colTotals[c.key])),
        fmt(pivot.all),
      ],
    ],
  };
}

/** A GitHub-flavoured markdown table (first column left, the rest right). */
export function markdownTable({ head, rows }) {
  if (!head?.length) return "";
  const line = (cells) => `| ${cells.map(cell).join(" | ")} |`;
  const sep = `|${head.map((_, i) => (i === 0 ? " --- " : " ---: ")).join("|")}|`;
  return [line(head), sep, ...rows.map(line)].join("\n");
}

/**
 * The short report `Copy as Markdown` puts on the clipboard:
 * title `**<dataset> · <runs> · <measure>**`, the verdict sentence, the gap
 * lines or the pivot table, a link back.
 * @param {{dataset: string, runs: string[], measure: string,
 *   sentence?: string, gap?: {title: string, lines: string[]},
 *   table?: {head: string[], rows: string[][]}, link: string}} report
 */
export function workbenchMarkdown({
  dataset,
  runs,
  measure,
  sentence,
  gap,
  table,
  link,
}) {
  const parts = [
    `**${[dataset, (runs ?? []).join(", "), measure].filter(Boolean).join(" · ")}**`,
  ];
  if (sentence) parts.push(sentence);
  if (gap?.lines?.length)
    parts.push(
      [`**${gap.title}**`, ...gap.lines.map((l) => `- ${l}`)].join("\n"),
    );
  if (table?.head?.length) parts.push(markdownTable(table));
  if (link) parts.push(`[Open in Future AGI](${link})`);
  return `${parts.join("\n\n")}\n`;
}

const csvCell = (v) => {
  const s = String(v ?? "");
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

/** CSV of the visible table (header + rows). */
export function tableCsv({ head, rows }) {
  return `${[head, ...rows].map((r) => r.map(csvCell).join(",")).join("\n")}\n`;
}
