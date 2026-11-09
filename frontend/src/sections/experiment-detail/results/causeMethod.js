// options-truth L5 G5 (W0-3): root causes come from rules over the runs'
// config changes and their failed evals, not from a model. Every surface
// that shows a cause (the Why strip, Why ▾, the Summary cause card) names
// the method in these words.
export const RULES_METHOD_LABEL =
  "Rule-based: from config changes and failed evals";

/** The method line for a cause: confirmed by a rerun, else rule-based. */
export const causeMethodLabel = (cause) =>
  cause?.status === "confirmed" && cause?.status_label
    ? cause.status_label
    : RULES_METHOD_LABEL;
