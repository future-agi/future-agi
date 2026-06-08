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

const normalizeSpanLevel = (sl) => {
  if (!sl) return null;
  const evaluated = sl.evaluated_count ?? null;
  const inScope = sl.in_scope_count ?? null;
  const errored = sl.errored_count ?? 0;
  const outcomes = sl.outcomes || {};
  const mean = sl.mean ?? sl.score ?? null;
  const notEvaluated =
    inScope != null && evaluated != null
      ? Math.max(0, inScope - evaluated - errored)
      : 0;
  return { outcomes, evaluated, inScope, errored, mean, notEvaluated };
};

// Maps the backend's computed eval_results rollup into the cell model.
export const adaptEvalCell = (raw, col) => {
  const kind = resolveEvalKind(col);
  if (raw == null || raw === "") return null;
  if (typeof raw !== "object" || Array.isArray(raw)) return null;
  if (!("trace_level" in raw) && !("span_level" in raw)) return null;

  const tl = raw.trace_level;
  return {
    kind,
    traceLevel: tl
      ? { outcome: tl.outcome ?? null, value: tl.value ?? null }
      : null,
    spanLevel: normalizeSpanLevel(raw.span_level),
  };
};

export const choiceTone = (label, col) =>
  (col?.choicesMap || {})[label] || "neutral";
