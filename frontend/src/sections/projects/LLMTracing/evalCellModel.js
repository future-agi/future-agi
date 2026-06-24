export const EVAL_KIND = {
  PASS_FAIL: "passfail",
  CHOICE: "choice",
  NUMERIC: "numeric",
};

export const resolveEvalKind = (col) => {
  const t = String(col?.outputType || "").toLowerCase();
  if (t === "pass/fail" || t === "pass_fail" || t === "boolean")
    return EVAL_KIND.PASS_FAIL;
  if (t === "choices" || t === "choice") return EVAL_KIND.CHOICE;
  return EVAL_KIND.NUMERIC;
};

export const choiceTone = (label, col) =>
  (col?.choicesMap || {})[label] || "neutral";

export const NUMERIC_PASS_CUTOFF = 50;
export const isNumericPass = (n) =>
  typeof n === "number" && n >= NUMERIC_PASS_CUTOFF;
export const scoreTone = (n) => (isNumericPass(n) ? "pass" : "fail");

// Render the backend's flat eval cell value straight into chips — no wrapper.
// Pass/Fail -> {pass,fail} counts; Choices -> {label:count}; Score -> number.
// Also accepts a scalar (Pass/Fail "pass"/"fail"/number, a single choice
// string/array) as a defensive fallback.
export const evalCellChips = (value, col) => {
  if (value == null || value === "") return [];
  if (typeof value === "object" && !Array.isArray(value) && value.error) {
    return [{ label: "Error", tone: "errored" }];
  }
  const kind = resolveEvalKind(col);

  if (kind === EVAL_KIND.PASS_FAIL) {
    if (typeof value === "object" && !Array.isArray(value)) {
      const chips = [];
      if (value.fail) chips.push({ label: `Fail ${value.fail}`, tone: "fail" });
      if (value.pass) chips.push({ label: `Pass ${value.pass}`, tone: "pass" });
      return chips;
    }
    const passed =
      value === "pass" || value === true || isNumericPass(value);
    return [{ label: passed ? "Pass" : "Fail", tone: passed ? "pass" : "fail" }];
  }

  if (kind === EVAL_KIND.CHOICE) {
    if (typeof value === "object" && !Array.isArray(value)) {
      return Object.entries(value)
        .filter(([, n]) => n > 0)
        .sort((a, b) => b[1] - a[1])
        .map(([label, n]) => ({
          label: `${label} ${n}`,
          tone: choiceTone(label, col),
        }));
    }
    const labels = Array.isArray(value) ? value : [value];
    return labels
      .filter((l) => l != null && l !== "")
      .map((l) => ({ label: String(l), tone: choiceTone(String(l), col) }));
  }

  // Score
  if (typeof value === "number")
    return [{ label: `${Number(value.toFixed(2))}%`, tone: scoreTone(value) }];
  return [{ label: String(value), tone: "plain" }];
};
