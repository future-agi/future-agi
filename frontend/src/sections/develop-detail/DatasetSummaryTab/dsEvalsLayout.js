// The dataset Summary evals table's columns (one view, A1-37): the catalog,
// its default set and the shared layout hook (the table and the card
// header's Columns menu use the same per-dataset layout).
import useColumnLayout from "src/sections/common/ux/oneview/useColumnLayout";

export const DS_EVALS_SURFACE = "dsEvals";

export const DS_EVALS_TIPS = {
  direction: "Direction: whether a higher score is better or worse.",
  passRate:
    "Share of scored rows that pass the eval's threshold. Lowest first.",
  mean: "Average score. Pass/Fail evals have no mean.",
  scored: "Rows with a result, of all rows in view.",
  errors: "Rows where the eval errored (not counted in the pass rate).",
  agreement:
    "How often the eval agrees with your reviewers' labels, and Cohen's kappa.",
};

/** The catalog, in table order; widths drive the table's minWidth. */
export const DS_EVALS_COLUMNS = Object.freeze([
  { id: "name", label: "Eval", locked: true, sortable: true, width: 200 },
  {
    id: "version",
    label: "Version",
    defaultVisible: false,
    sortable: false,
    width: 110,
  },
  {
    id: "direction",
    label: "Dir.",
    defaultVisible: false,
    sortable: false,
    description: DS_EVALS_TIPS.direction,
    width: 56,
  },
  {
    id: "passRate",
    label: "Pass rate",
    sortable: true,
    align: "right",
    width: 128,
  },
  {
    id: "mean",
    label: "Mean",
    sortable: true,
    align: "right",
    description: DS_EVALS_TIPS.mean,
    width: 72,
  },
  { id: "dist", label: "Distribution", sortable: false, width: 116 },
  {
    id: "scored",
    label: "Scored",
    sortable: true,
    align: "right",
    description: DS_EVALS_TIPS.scored,
    width: 92,
  },
  {
    id: "errors",
    label: "Errors",
    sortable: true,
    align: "right",
    description: DS_EVALS_TIPS.errors,
    width: 72,
  },
  {
    id: "agreement",
    label: "Judge vs human",
    defaultVisible: false,
    sortable: true,
    description: DS_EVALS_TIPS.agreement,
    width: 136,
  },
  { id: "failing", label: "Failing rows", sortable: true, width: 176 },
]);

/** The table's min width = what its visible columns need (A1-37). */
export const dsEvalsMinWidth = (visibleIds) =>
  DS_EVALS_COLUMNS.filter((c) => visibleIds.includes(c.id)).reduce(
    (s, c) => s + (c.width ?? 100),
    0,
  );

/** The dataset Summary evals layout (table + the card's Columns menu). */
export function useDsEvalsLayout(datasetId, { commands = true } = {}) {
  return useColumnLayout({
    surface: DS_EVALS_SURFACE,
    scopeId: datasetId,
    columns: DS_EVALS_COLUMNS,
    commands,
  });
}
