// 8f: what `Copy link` puts on the clipboard and says. The link
// drops what only makes sense here (`from`, the personal back-path) and,
// unless the viewer keeps it, the open row; the toast lists what the link
// holds. IT-R1 G2 P6: `proto_stage` STAYS — the prototype stage is per tab,
// so a link without it opens on `fresh`, where these runs do not exist.
import { fmtCount } from "src/sections/common/format/fmt";
import { isPrototypeMode } from "src/utils/prototype";
import { sortLabel } from "./sortModel";

// Leak scan (options-truth L3 G10): the stage's query key and storage key
// exist only in prototype builds (and tests). The check is static, so a
// production bundle folds it to false and ships neither string.
const PROTOTYPE_BUILD =
  import.meta.env.MODE === "test" ||
  (import.meta.env.VITE_ENVIRONMENT === "local" &&
    import.meta.env.VITE_PROTOTYPE_MODE === "true");
const STAGE_PARAM = PROTOTYPE_BUILD ? "proto_stage" : null;
const STAGE_STORAGE_KEY = PROTOTYPE_BUILD ? "fa.proto.stage" : null;

/** The tab's prototype stage (prototype mode only; null otherwise). */
export function currentProtoStage() {
  if (!STAGE_STORAGE_KEY || !isPrototypeMode() || typeof window === "undefined")
    return null;
  try {
    return window.sessionStorage.getItem(STAGE_STORAGE_KEY);
  } catch {
    return null;
  }
}

/** Query keys a shared link never carries. */
export const LOCAL_ONLY_KEYS = Object.freeze(["from"]);
/** Keys that only describe the open row (dropped unless included). */
export const ROW_KEYS = Object.freeze([
  "row",
  "run",
  "dtab",
  "trial",
  "tview",
  "tspan",
]);

/**
 * @param {string} href  the page URL
 * @param {{includeRow?: boolean, stage?: string|null}} [opts]  `stage`: the
 *   prototype stage to carry when the URL has none (default: this tab's)
 * @returns {string}
 */
export function shareLinkOf(
  href,
  { includeRow = true, stage = currentProtoStage() } = {},
) {
  const u = new URL(href);
  LOCAL_ONLY_KEYS.forEach((k) => u.searchParams.delete(k));
  if (STAGE_PARAM && stage && !u.searchParams.get(STAGE_PARAM))
    u.searchParams.set(STAGE_PARAM, stage);
  if (!includeRow) ROW_KEYS.forEach((k) => u.searchParams.delete(k));
  return u.toString();
}

const TAB_LABELS = {
  data: "Data",
  matrix: "Matrix",
  summary: "Summary",
  paths: "Paths",
};
// IT-R5 G6 S3 (R13): the grid's word, `Worse` (never `Regressed`).
const SHOW_LABELS = {
  regressed: "Worse",
  improved: "Improved",
  changed: "Changed",
  differ: "Differ",
};

/**
 * `2 runs · baseline #2B · Data · Worse filter · row T12 open`; with `sort`
 * (the URL `sort`): `… · sorted by Biggest regression`.
 * @param {{runCount: number, baselineLabel?: string, tab?: string,
 *   show?: string, filters?: number, rowLabel?: string|null,
 *   sort?: string|null}} args
 */
export function shareLinkSummary({
  runCount,
  baselineLabel,
  tab,
  show,
  filters,
  rowLabel,
  sort,
  search,
}) {
  const sorted = sort ? sortLabel(sort).replace(/^Sort: /, "") : null;
  // L7c g6: `Row 141 open` (the label already says Row), never `row Row`.
  const rowText = rowLabel
    ? /^row\b/i.test(String(rowLabel))
      ? `${rowLabel} open`
      : `row ${rowLabel} open`
    : null;
  return [
    fmtCount(runCount, "run"),
    baselineLabel ? `baseline ${baselineLabel}` : null,
    TAB_LABELS[tab] ?? null,
    show && show !== "all" ? `${SHOW_LABELS[show] ?? show} filter` : null,
    filters ? fmtCount(filters, "filter") : null,
    sorted && sorted !== "Sort" ? `sorted by ${sorted}` : null,
    search ? `search "${search}"` : null,
    rowText,
  ]
    .filter(Boolean)
    .join(" · ");
}
