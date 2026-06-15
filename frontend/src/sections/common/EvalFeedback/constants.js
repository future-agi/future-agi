export const STAGE = Object.freeze({
  ENTRY: "entry",
  ACTION: "action",
});

// Template output_type values the entry stage knows how to render.
// "reason" → free-text, "score" → numeric, "Pass/Fail" / "choices" → radio.
// "select" is reserved for future use; today it falls through to a TODO.
export const OUTPUT_TYPES = Object.freeze({
  REASON: "reason",
  SCORE: "score",
  PASS_FAIL: "Pass/Fail",
  CHOICES: "choices",
  SELECT: "select",
});
