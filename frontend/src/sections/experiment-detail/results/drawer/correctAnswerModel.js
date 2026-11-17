// W4 §9 (M6, the results integration): "Correct this answer" in the
// results drawer's Review tab. Pure: the V2 result-review body that saves a
// reviewer's corrected output (and, with "Also set as expected", suggests it
// to the dataset as the gold answer), and the toast that confirms it.

/** The V2 body, or null when there is nothing to save. */
export function correctionBody({
  exampleId,
  runId,
  baseRunId = null,
  pairwise = false,
  text,
  setAsExpected = false,
}) {
  const value = typeof text === "string" ? text : "";
  if (!exampleId || !runId || !value.trim()) return null;
  return {
    example_id: exampleId,
    ...(pairwise
      ? {
          mode: "pairwise",
          run_a: baseRunId,
          run_b: runId,
          corrected_run: runId,
        }
      : { mode: "rubric", run_id: runId }),
    values: [],
    corrected_output: value,
    set_as_expected: Boolean(setAsExpected),
  };
}

/** True when the edit says something the answer did not. */
export const changedFrom = (original, text) =>
  String(text ?? "").trim() !== "" &&
  String(text ?? "").trim() !== String(original ?? "").trim();

/** `Correction saved on #4A's answer · suggested as the expected answer`. */
export function correctionToast(runLabel, result) {
  const base = `Correction saved on ${runLabel ?? "this run"}'s answer`;
  return result?.correction_id
    ? `${base} · suggested as the expected answer`
    : base;
}
