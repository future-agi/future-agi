import useFalconStore from "../store/useFalconStore";

// Ask Falcon about a comparison (PRODUCT-SPEC §3.13 C22). Same contract as
// openFixWithFalcon: prefill the sidebar's input with the question and a
// context block, open the sidebar; the user presses send.

export const ANALYSIS_CONTEXT_HEADER = "Comparison context:";

/** The three suggested questions for a comparison (labels from the view). */
export function analysisQuestions({
  candidateLabel,
  regressedMetricName,
} = {}) {
  return [
    "Summarize this comparison",
    candidateLabel && regressedMetricName
      ? `Why did ${candidateLabel} regress on ${regressedMetricName}?`
      : "Why did the candidate regress?",
    "What should I fix next?",
  ];
}

const line = (k, v) =>
  v === undefined || v === null || v === "" ? null : `- ${k}: ${v}`;

/**
 * The context block Falcon reads: ids, labels, baseline, filters and the URL.
 * @param {{datasetId, experimentId?, runs?: {run_id,label,name}[],
 *   runIds?: string[], baselineId?, rowId?: string, filters?: object[],
 *   url?: string}} ctx
 */
export function buildAnalysisContext({
  datasetId,
  experimentId,
  runs = [],
  runIds,
  baselineId,
  rowId,
  filters,
  url,
}) {
  // IT-R3 G2 (S3, additive): `runIds` + `rowId` from a task's side by side.
  const list = runs.length
    ? runs
    : (runIds ?? []).map((id) => ({ run_id: id, label: id }));
  const base = list.find((r) => r.run_id === baselineId);
  return [
    ANALYSIS_CONTEXT_HEADER,
    line("dataset_id", datasetId),
    line("experiment_id", experimentId),
    line("runs", list.map((r) => `${r.label}=${r.run_id}`).join(", ")),
    line("baseline", base ? `${base.label}=${base.run_id}` : "none"),
    line("row_id", rowId),
    line("filters", filters?.length ? JSON.stringify(filters) : null),
    line("analysis_url", url),
  ]
    .filter(Boolean)
    .join("\n");
}

/** Prefill the Falcon sidebar with a question about the comparison and open it. */
export function openFalconAnalysis({ question, context }) {
  const prompt = [question, "", buildAnalysisContext(context)].join("\n");
  const { setPendingPrompt, openSidebar } = useFalconStore.getState();
  setPendingPrompt(prompt);
  openSidebar();
}
