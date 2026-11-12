// The results read refuses runs that cannot be lined up row by row
// (model_hub results_selection.select_results, 409): "Runs require the same
// immutable version, trial plan and evaluator bindings; mixed comparison is
// not supported." Any two runs whose dataset version, trial plan or
// evaluators differ hit it, inside one experiment or across experiments.
// The app says it in plain words instead. Pure.

/** What the compare page and its toast say for that refusal. */
export const MIXED_RUNS_WORDS =
  "These runs can't be lined up row by row: they use different dataset versions, trial plans or evaluators. Compare runs that share all three.";

/** One toast for the refusal, however many reads on the page receive it. */
export const MIXED_RUNS_TOAST_KEY = "results-mixed-runs-refusal";

const MIXED_RUNS_REFUSAL =
  /mixed comparison is not supported|same immutable version, trial plan and evaluator bindings/i;

/** An experiment's page (its Data tab), for the notice's link. */
export const experimentDataHref = (datasetId, experimentId) =>
  `/dashboard/develop/experiment/${experimentId}/data${
    datasetId ? `?datasetId=${encodeURIComponent(datasetId)}` : ""
  }`;

/** The server's sentence in a rejected read (app client or axios shape). */
export function refusalText(error) {
  if (!error) return "";
  if (typeof error === "string") return error;
  const data = error.response?.data;
  const text = [error.result, data?.result, data?.message, error.message].find(
    (v) => typeof v === "string" && v,
  );
  return text ?? "";
}

/** True for the results read's mixed-runs refusal (matched on its words). */
export const isMixedRunsRefusal = (error) =>
  MIXED_RUNS_REFUSAL.test(refusalText(error));
