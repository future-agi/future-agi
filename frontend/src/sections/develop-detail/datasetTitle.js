// Browser-tab titles for the dataset pages: what you are on first, then
// where, then the product (`Support agent - golden set · Data · Future AGI`).

export const PRODUCT = "Future AGI";

const TAB_LABELS = {
  data: "Data",
  experiments: "Experiments",
  summary: "Summary",
  optimization: "Optimization",
};

/** Title of a dataset page; `Dataset` until the name loads. */
export function datasetTitle(name, tab, compare = false) {
  const label = TAB_LABELS[tab] ?? TAB_LABELS.data;
  const what = String(name ?? "").trim() || "Dataset";
  return [compare ? `Compare · ${what}` : what, label, PRODUCT].join(" · ");
}

/** Title of the dataset list. */
export const DATASETS_TITLE = `Datasets · ${PRODUCT}`;
