// Shared helpers for the eval-feedback "right value" input. Framework-free so
// they can be unit-tested directly and reused by both the dataset feedback
// drawer and the evals → usage feedback drawer.

export const FEEDBACK_OUTPUT_TYPES = {
  REASON: "reason",
  SCORE: "score",
  PASS_FAIL: "Pass/Fail",
  CHOICES: "choices",
  SELECT: "select",
};

// Coerce a feedback/eval value into an array of choices. Multi-choice values
// arrive as a JSON-encoded string ("[\"A\",\"B\"]") or already as an array.
export const toArray = (value) => {
  if (Array.isArray(value)) return value;
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed;
    } catch {
      /* not JSON — treat as a single value below */
    }
    return value ? [value] : [];
  }
  return value === null || value === undefined ? [] : [value];
};

// The eval's explanation for this cell. Cells store value-infos under either
// `value_infos` or `valueInfos`, and the explanation under `reason` or
// `summary` — mirror the eval panel's own fallback chain.
export const getReason = (data) => {
  const info = data?.valueInfos ?? data?.value_infos ?? {};
  return info.reason || info.summary || "";
};
