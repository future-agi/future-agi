// The Data tab's help copy (SVP-4.2). Pure; tested. ONE source: the grid
// footer (one view, A1-09) builds its line from `dataHelpText()`.
import { fmtCount } from "src/sections/common/format/fmt";
import { isSurfaceShown } from "src/launch/launchSwitch";

const has = (cols, role) =>
  (cols ?? []).some((c) => c?.role === role && c?.is_visible !== false);

const wordOf = (kind) =>
  kind === "task_suite"
    ? "task"
    : kind === "conversations"
      ? "conversation"
      : "row";

/**
 * `30 tasks. Each has an input, an expected result and a start state.`
 * `248 rows. Each has an input and an expected result.`
 * Also the parts the footer joins: `count` (`30 tasks`) and `each`
 * (`each has an input, an expected result and a start state`).
 */
/**
 * The expected column a row dataset is judged against (the first visible
 * one) and how many rows have no value there: saved rows from its
 * `stats.empty_count`, pending new rows from their cells (options-truth L3
 * G7 W7: never "each has an expected result" when some do not).
 * @returns {{column: string, missing: number, pending: number}|null}
 */
export function missingExpected(columns, pendingRows = []) {
  const col = (columns ?? []).find(
    (c) => c?.role === "expected" && c?.is_visible !== false,
  );
  if (!col) return null;
  const saved = Math.max(0, Number(col.stats?.empty_count) || 0);
  const pending = (pendingRows ?? []).filter((r) => {
    const v = r?.cells?.[col.id];
    return v == null || v === "";
  }).length;
  return { column: col.name, missing: saved + pending, pending };
}

export function dataHelpText({
  kind,
  total,
  columns,
  failedRows,
  pendingRows = [],
} = {}) {
  const word = wordOf(kind);
  const parts = [];
  const gap =
    kind === "task_suite" ? null : missingExpected(columns, pendingRows);
  if (has(columns, "input") || kind === "task_suite") parts.push("an input");
  if (kind === "task_suite" || (has(columns, "expected") && !gap?.missing))
    parts.push("an expected result");
  if (kind === "task_suite") parts.push("a start state");
  // L6 launch switch: no run hint while New experiment (and the column
  // role controls it points at) are hidden.
  const runHint = isSurfaceShown("exp.new")
    ? "Mark an input and an expected column to run experiments"
    : null;
  const eachText = parts.length
    ? `Each has ${parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}` : parts[0]}`
    : runHint;
  // Rows (saved and new) without an expected value are counted, by name.
  const gapText = gap?.missing
    ? `${fmtCount(gap.missing, word)}${gap.pending ? ` (${fmtCount(gap.pending)} new)` : ""} ${gap.missing === 1 ? "has" : "have"} no ${gap.column}`
    : null;
  const each =
    total || pendingRows.length
      ? parts.length
        ? [eachText, gapText].filter(Boolean).join(" · ")
        : gapText ?? eachText
      : "Add your first row to start";
  const count = fmtCount(total ?? 0, word);
  return {
    lead: each ? `${count}. ${each}.` : `${count}.`,
    count,
    each: each ? each.charAt(0).toLowerCase() + each.slice(1) : "",
    failed: failedRows ? failedRows : null,
  };
}

/**
 * The grid footer (A1-09): the help line folded into the status bar, no
 * extra row above the grid.
 *   `30 tasks · each has an input, an expected result and a start state ·
 *    3 fail at least one check · v2`
 * Filtered → `12 of 30 tasks (filtered)`; pending rows → `+ 2 new, unsaved`;
 * a selection → `3 selected`; no failures → the failed part is dropped.
 *
 * @returns {{count: string, each: string, failed: number|null,
 *   selected: string|null, version: string|null}}
 */
export function dataFooterParts({
  kind,
  total,
  shown,
  columns,
  failedRows,
  selected,
  version,
  added = 0,
  pendingRows = [],
} = {}) {
  const help = dataHelpText({ kind, total, columns, failedRows, pendingRows });
  const filtered = shown != null && total != null && shown !== total;
  let count = filtered
    ? `${fmtCount(shown)} of ${fmtCount(total, wordOf(kind))} (filtered)`
    : help.count;
  if (added > 0) count += ` + ${fmtCount(added)} new, unsaved`;
  return {
    count,
    each: help.each,
    failed: help.failed,
    selected: selected ? `${fmtCount(selected)} selected` : null,
    version: version != null ? `v${version}` : null,
  };
}

/** The footer as plain text (tests, aria, copy): parts joined by ` · `. */
export function dataFooterText(args) {
  const p = dataFooterParts(args);
  return [
    p.count,
    p.each,
    p.failed ? `${fmtCount(p.failed)} fail at least one check` : null,
    p.selected,
    p.version,
  ]
    .filter(Boolean)
    .join(" · ");
}
