import {
  resolveEvalKind,
  EVAL_KIND,
  choiceTone,
  scoreTone,
  isNumericPass,
} from "src/sections/projects/LLMTracing/evalCellModel";

export const NAME_W = "42%";

// `col` shim so the shared evalCellModel chip helpers work outside the grid.
export const colFromEval = (ev) => ({
  id: ev.eval_config_id,
  name: ev.eval_name,
  outputType: ev.output_type,
  choicesMap: ev.choices_map ?? {},
});

export const spanResultChip = (span, outputType, choicesMap = {}) => {
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
      tone: choiceTone(labels[0] || "", { choicesMap }),
    };
  }
  if (kind === EVAL_KIND.PASS_FAIL) {
    if (span.value === "pass") return { label: "Pass", tone: "pass" };
    if (span.value === "fail") return { label: "Fail", tone: "fail" };
    return { label: "—", tone: "plain" };
  }
  return {
    label: span.value != null ? `${span.value}%` : "—",
    tone: typeof span.value === "number" ? scoreTone(span.value) : "plain",
  };
};

// Choices have nothing to "fix", so they always count as passed.
export const spanPassed = (span, outputType) => {
  if (span.error) return false;
  const kind = resolveEvalKind({ outputType });
  if (kind === EVAL_KIND.PASS_FAIL) return span.value === "pass";
  if (kind === EVAL_KIND.NUMERIC) return isNumericPass(span.value);
  return true;
};

// A span expands when it has an explanation, errored, or failed — failures open
// so the inline Fix-with-Falcon CTA is reachable (same gate as the CTA itself),
// and the localizer fetches deeper detail via get_evaluation_details
// (span_id + config_id). Passing and choice evals stay collapsed (nothing to fix).
export const spanHasDetail = (span, outputType) =>
  !!(span.explanation || span.error) || !spanPassed(span, outputType);

// Keyboard parity for the click-to-expand rows and CTAs in this section. These
// are plain Boxes, so without this they are unreachable by keyboard and screen
// readers get no affordance or state (review: cdileep23).
//
// `expanded` adds aria-expanded for disclosure rows; omit it for plain buttons.
// `enabled: false` returns nothing, so a row that cannot expand stays out of
// the tab order rather than offering a no-op stop.
export const activatableProps = (
  onActivate,
  { expanded, enabled = true } = {},
) => {
  if (!enabled || typeof onActivate !== "function") return {};
  return {
    role: "button",
    tabIndex: 0,
    ...(expanded === undefined ? {} : { "aria-expanded": expanded }),
    onClick: onActivate,
    onKeyDown: (e) => {
      // Space scrolls the page by default; Enter/Space are the expected
      // activation keys for role="button".
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onActivate(e);
      }
    },
  };
};
