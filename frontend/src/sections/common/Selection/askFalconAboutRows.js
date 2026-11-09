// W5-7 (W5-SCOPE §7 C3): Ask Falcon about selected rows, by reference. The
// question carries {dataset_id, run ids, row ids} in the context block as a
// `rows` pill (the same pill the results filter reads), never the rows'
// text. The chip text says what was sent: `6 rows from #2B vs #2A`.
import {
  buildAnalysisContext,
  openFalconAnalysis,
} from "src/sections/falcon-ai/helpers/openFalconAnalysis";
import { rowsPill as resultsRowsPill } from "src/sections/common/Results/filterPills";

const plural = (n, word) =>
  `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;

/** `6 rows from #2B vs #2A`, `1 row from #1A`. */
export function rowsContextLabel(count, runs = [], baselineId = null) {
  const base = runs.find((r) => r.run_id === baselineId);
  const others = runs
    .filter((r) => r.run_id !== baselineId)
    .map((r) => r.label ?? r.run_id);
  const head = plural(count, "row");
  if (!others.length) return base ? `${head} from ${base.label}` : head;
  return `${head} from ${others.join(", ")}${base ? ` vs ${base.label}` : ""}`;
}

/** The results `rows` pill for these ids (`{type, ids, label}`): the one
 * pill builder (`filterPills.rowsPill`), so Falcon and the grid read the
 * same pill. */
export const rowsPill = (ids, label) => resultsRowsPill(ids, { label });

/** The question the sidebar is prefilled with (the user can edit it). */
export const rowsQuestion = (count) =>
  count === 1
    ? "What happened on this row?"
    : `What do these ${count.toLocaleString()} rows have in common?`;

/** The context object: ids only, the runs in view, the selection as a pill. */
export function rowsFalconContext({
  datasetId,
  experimentId,
  runs = [],
  baselineId,
  rowIds,
  url,
}) {
  const ids = [...new Set(rowIds ?? [])];
  return {
    datasetId,
    experimentId,
    runs: runs.map((r) => ({ run_id: r.run_id, label: r.label ?? r.run_id })),
    baselineId,
    filters: [rowsPill(ids, rowsContextLabel(ids.length, runs, baselineId))],
    url,
  };
}

/** The exact prompt the sidebar receives (question + context block). */
export const rowsFalconPrompt = (args) =>
  [
    rowsQuestion(new Set(args.rowIds ?? []).size),
    "",
    buildAnalysisContext(rowsFalconContext(args)),
  ].join("\n");

/** Open Falcon with the question and the rows by reference. */
export function askFalconAboutRows(args) {
  const context = rowsFalconContext(args);
  openFalconAnalysis({
    question: rowsQuestion(context.filters[0].ids.length),
    context,
  });
  return context;
}
