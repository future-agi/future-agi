import {
  resolveEvalKind,
  EVAL_KIND,
  choiceTone,
} from "src/sections/projects/LLMTracing/evalCellModel";

export const NAME_W = "42%";

// `col` shim so the shared evalCellModel chip helpers work outside the grid.
export const colFromEval = (ev) => ({
  id: ev.eval_config_id,
  name: ev.eval_name,
  outputType: ev.output_type,
  choicesMap: {},
});

// Reshape the backend `aggregate` into the {span_level} cell that the shared
// adaptEvalCell/buildChips already render.
export const evalToCell = (ev) => {
  const spans = ev.spans || [];
  const errored = spans.filter((s) => s.error).length;
  const kind = resolveEvalKind({ outputType: ev.output_type });
  return {
    trace_level: null,
    span_level: {
      outcomes: kind === EVAL_KIND.NUMERIC ? {} : ev.aggregate || {},
      evaluated_count: spans.length - errored,
      in_scope_count: spans.length,
      errored_count: errored,
      mean: kind === EVAL_KIND.NUMERIC ? (ev.aggregate ?? null) : null,
    },
  };
};

export const spanResultChip = (span, outputType) => {
  if (span.error) return { label: "Errored", tone: "errored" };
  const kind = resolveEvalKind({ outputType });
  if (kind === EVAL_KIND.CHOICE) {
    const labels = Array.isArray(span.value)
      ? span.value
      : span.value != null
        ? [span.value]
        : [];
    return {
      label: labels.length ? labels.join(", ") : "—",
      tone: choiceTone(labels[0] || "", {}),
    };
  }
  if (kind === EVAL_KIND.PASS_FAIL) {
    if (span.value === "pass") return { label: "Pass", tone: "pass" };
    if (span.value === "fail") return { label: "Fail", tone: "fail" };
    return { label: "—", tone: "plain" };
  }
  return { label: span.value != null ? `${span.value}%` : "—", tone: "plain" };
};

// Choices have nothing to "fix", so they always count as passed.
export const spanPassed = (span, outputType) => {
  if (span.error) return false;
  const kind = resolveEvalKind({ outputType });
  if (kind === EVAL_KIND.PASS_FAIL) return span.value === "pass";
  if (kind === EVAL_KIND.NUMERIC)
    return typeof span.value === "number" && span.value >= 50;
  return true;
};

// A span expands when it has an explanation or errored — the localizer then
// fetches deeper detail via get_evaluation_details (span_id + config_id).
export const spanHasDetail = (span) => !!(span.explanation || span.error);
