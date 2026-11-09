// Classification (W5-4 B3): the view's words, cell shading and the rows pill
// of a matrix cell. Pure. Every number comes from the mock's
// w5Classification.js.

/** The react-query key of a dataset's classification reads (one key). */
export const CLASSIFICATION_KEY = "w5-classification";

export const pct1 = (v) =>
  Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : "—";
export const f2 = (v) => (Number.isFinite(v) ? v.toFixed(2) : "—");

/** `81.3% (75.2–86.1%)` */
export function accuracyText(acc) {
  if (!acc || acc.value == null) return "—";
  return `${pct1(acc.value)} (${(acc.ci.low * 100).toFixed(1)}–${(acc.ci.high * 100).toFixed(1)}%)`;
}

/** A confusion-matrix cell: `Truth refund → predicted billing (4)`. */
export const cellLabel = (cell) => `${cellPillLabel(cell)} (${cell.count})`;

/** The rows pill label of a cell (the pill shows its own row count). */
export const cellPillLabel = (cell) =>
  `Truth ${cell.truth} → predicted ${cell.predicted}`;

/** Cell lookup by truth × predicted. */
export function cellIndex(matrix) {
  const m = new Map();
  (matrix?.cells ?? []).forEach((c) =>
    m.set(`${c.truth}\u0001${c.predicted}`, c),
  );
  return (truth, predicted) => m.get(`${truth}\u0001${predicted}`) ?? null;
}

/** A cell's share of its truth row (0..1), for the heat shading. */
export function rowShare(matrix, cell) {
  if (!cell) return 0;
  const total = (matrix?.cells ?? [])
    .filter((c) => c.truth === cell.truth)
    .reduce((s, c) => s + c.count, 0);
  return total ? cell.count / total : 0;
}

/** The largest off-diagonal cell (the most common confusion), or null. */
export function topConfusion(matrix) {
  return (
    [...(matrix?.cells ?? [])]
      .filter((c) => c.truth !== c.predicted)
      .sort(
        (a, b) =>
          b.count - a.count ||
          a.truth.localeCompare(b.truth) ||
          a.predicted.localeCompare(b.predicted),
      )[0] ?? null
  );
}

/** `Truth: intent · Predicted: output.intent · Positive class: refund` */
export function setupLine(setup, columns = []) {
  if (!setup) return "";
  const col =
    columns.find((c) => c.id === setup.truth_column)?.name ??
    setup.truth_column;
  return [
    `Truth: ${col}`,
    `Predicted: ${setup.predicted}`,
    setup.positive ? `Positive class: ${setup.positive}` : null,
    Object.keys(setup.label_map ?? {}).length
      ? `${Object.keys(setup.label_map).length} predicted labels mapped`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** Predicted labels that are not truth labels (the ones worth mapping). */
export const unmatchedLabels = (predicted = [], truth = []) =>
  predicted.filter((l) => !truth.includes(l));

export const PREDICTED_HELP =
  "output, or a path into a JSON output such as output.intent";
