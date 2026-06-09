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

const trimNum = (n) =>
  typeof n === "number" ? `${Number(n.toFixed(2))}` : `${n}`;

const spanLevelChips = (model, col) => {
  const { kind } = model;
  const { outcomes, evaluated, errored, mean, notEvaluated } = model.spanLevel;
  const chips = [];
  const denom =
    evaluated ?? (Object.values(outcomes).reduce((a, b) => a + b, 0) || null);

  if (kind === EVAL_KIND.PASS_FAIL) {
    if (outcomes.fail) chips.push({ label: `Fail ${outcomes.fail}`, tone: "fail" });
    if (errored) chips.push({ label: `Errored ${errored}`, tone: "errored" });
    if (outcomes.pass) chips.push({ label: `Pass ${outcomes.pass}`, tone: "pass" });
  } else if (kind === EVAL_KIND.CHOICE) {
    Object.entries(outcomes)
      .sort((a, b) => b[1] - a[1])
      .forEach(([label, count]) => {
        const pct = denom ? Math.round((count / denom) * 100) : null;
        chips.push({
          label: pct != null ? `${label} ${pct}%` : `${label} ${count}`,
          tone: choiceTone(label, col),
        });
      });
    if (errored) chips.push({ label: `Errored ${errored}`, tone: "errored" });
  } else {
    if (mean != null) chips.push({ label: `${trimNum(mean)}%`, tone: "plain" });
    if (errored) chips.push({ label: `Errored ${errored}`, tone: "errored" });
  }
  return { chips, notEvaluated };
};

const traceLevelChip = (model, col) => {
  const { outcome, value } = model.traceLevel;
  if (model.kind === EVAL_KIND.NUMERIC || (outcome == null && value != null))
    return { label: `${trimNum(value)}%`, tone: "plain" };
  if (outcome === "pass" || outcome === "fail")
    return { label: outcome === "pass" ? "Pass" : "Fail", tone: outcome };
  if (outcome === "errored") return { label: "Errored", tone: "errored" };
  if (outcome) return { label: outcome, tone: choiceTone(outcome, col) };
  return null;
};

// Model → display chips + "not evaluated" remainder. Pure data (no JSX) so it
// can be shared by the grid cell renderer and the drawer rollup view.
export const buildChips = (model, col) => {
  if (!model) return { chips: [], notEvaluated: 0 };
  if (model.spanLevel) return spanLevelChips(model, col);
  if (model.traceLevel) {
    const chip = traceLevelChip(model, col);
    return { chips: chip ? [chip] : [], notEvaluated: 0 };
  }
  return { chips: [], notEvaluated: 0 };
};
