// The one review rule (KH-SCOPE §6 B1). The annotate form, the results
// drawer's rubric card and the prototype server all call these, so a score
// the form refuses is refused by a scripted submit too, with the same words.
//
// Value shapes are label-value.js's: numeric {value}, star {rating},
// categorical {selected: []}, thumbs_up_down {value: "up"|"down"}, text {text}.
// Emptiness is isLabelValueEmpty (the only emptiness rule); every label of a
// queue needs a value (owner decision 2: `QueueLabel.required` is not honoured
// until Annotations decides what it means).
//
// The value checks follow the backend's own validators
// (model_hub/views/annotation_queues.py `_validate_*_annotation_value`):
// numeric min ≤ v ≤ max and on a step from min (the max itself is always
// allowed), whole stars 1..no_of_stars, configured categorical options (one
// unless multi_choice), thumbs up/down, text min/max length.
import { isLabelValueEmpty } from "./label-value";

// The numeric defaults the input applies when a label has no setting
// (label-input.jsx NumericInput: min ?? 0, max ?? 10, step ?? 1).
export const NUMERIC_DEFAULTS = Object.freeze({ min: 0, max: 10, step: 1 });
// StarInput's default (`settings.no_of_stars || 5`).
export const DEFAULT_STARS = 5;
// Float slack for the step check, so 0.3 counts as 3 steps of 0.1.
export const STEP_TOLERANCE = 1e-9;

const THUMBS = [
  { value: "up", text: "Yes" },
  { value: "down", text: "No" },
];

const finite = (v) => {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
};

/** A number as people write it: 0.1 + 0.2 reads 0.3, 1 reads 1. */
export const formatNumber = (n) => String(Number(Number(n).toPrecision(12)));

const listText = (items) => items.join(", ");

/** min / max / step of a numeric label, with the input's defaults. */
export function numericRule(settings = {}) {
  const min = finite(settings?.min) ?? NUMERIC_DEFAULTS.min;
  const max = finite(settings?.max) ?? NUMERIC_DEFAULTS.max;
  const raw = finite(settings?.step_size ?? settings?.step);
  const step = raw != null && raw > 0 ? raw : NUMERIC_DEFAULTS.step;
  return { min, max, step };
}

const starCount = (settings) => {
  const n = finite(settings?.no_of_stars);
  return n != null && n >= 1 ? Math.floor(n) : DEFAULT_STARS;
};

const optionText = (opt) => {
  if (typeof opt === "string") return opt;
  return opt?.label || opt?.value || "";
};

/** The labels a categorical label offers, in order. */
export const categoricalOptions = (settings) =>
  (settings?.options ?? []).map(optionText).filter(Boolean);

// The values a categorical label accepts: each option's label and value
// (the backend's _categorical_option_values).
const acceptedOptions = (settings) => {
  const out = new Set();
  (settings?.options ?? []).forEach((opt) => {
    if (typeof opt === "string") out.add(opt);
    else if (opt && typeof opt === "object") {
      if (opt.label != null) out.add(String(opt.label));
      if (opt.value != null) out.add(String(opt.value));
    }
  });
  return out;
};

const lengthRule = (settings) => {
  const min = finite(settings?.min_length);
  const max = finite(settings?.max_length);
  return {
    min: min != null && min > 0 ? Math.floor(min) : null,
    max: max != null && max > 0 ? Math.floor(max) : null,
  };
};

const problem = (label, kind, text) => ({
  label_id: label.label_id ?? label.id ?? null,
  name: label.name,
  kind,
  text,
});

/**
 * The problem with one number for a numeric label, or null.
 * `label` = {name, settings}; used by the input as the reviewer types.
 */
export function numberProblem(label, n) {
  const { min, max, step } = numericRule(label?.settings);
  const name = label?.name ?? "This score";
  const value = finite(n);
  if (value == null || value < min || value > max) {
    const entered = value == null ? String(n ?? "") : formatNumber(value);
    return problem(
      label ?? {},
      "range",
      `${name} must be between ${formatNumber(min)} and ${formatNumber(max)}${
        entered === "" ? "" : ` (you entered ${entered})`
      }.`,
    );
  }
  if (value !== max) {
    const steps = (value - min) / step;
    if (
      Math.abs(steps - Math.round(steps)) >
      STEP_TOLERANCE * Math.max(1, Math.abs(steps))
    ) {
      return problem(
        label ?? {},
        "step",
        `${name} moves in steps of ${formatNumber(step)} (you entered ${formatNumber(value)}).`,
      );
    }
  }
  return null;
}

/** The problem with a non-empty value, or null. */
export function valueProblem(label, value) {
  const name = label?.name ?? "This score";
  const settings = label?.settings ?? {};
  switch (label?.type) {
    case "numeric":
      return numberProblem(label, value?.value);
    case "star": {
      const stars = starCount(settings);
      const r = finite(value?.rating);
      if (r == null || !Number.isInteger(r) || r < 1 || r > stars)
        return problem(label, "stars", `${name} is 1 to ${stars} stars.`);
      return null;
    }
    case "categorical": {
      const raw = value?.selected;
      const selected = (Array.isArray(raw) ? raw : [raw]).map(String);
      const accepted = acceptedOptions(settings);
      const shown = categoricalOptions(settings);
      if (selected.some((s) => !accepted.has(s)))
        return problem(
          label,
          "choice",
          `${name} must be ${settings.multi_choice ? "chosen from" : "one of"} ${listText(shown)}.`,
        );
      if (!settings.multi_choice && selected.length > 1)
        return problem(
          label,
          "choice",
          `${name} takes one choice (you picked ${listText(selected)}).`,
        );
      return null;
    }
    case "thumbs_up_down":
      if (!THUMBS.some((t) => t.value === value?.value))
        return problem(
          label,
          "choice",
          `${name} must be ${THUMBS.map((t) => t.text).join(" or ")}.`,
        );
      return null;
    case "text": {
      const { min, max } = lengthRule(settings);
      const length = String(value?.text ?? "").length;
      if (min != null && length < min)
        return problem(
          label,
          "length",
          `${name} needs at least ${min} characters (you wrote ${length}).`,
        );
      if (max != null && length > max)
        return problem(
          label,
          "length",
          `${name} takes at most ${max} characters (you wrote ${length}).`,
        );
      return null;
    }
    default:
      return null;
  }
}

/**
 * Every problem with a set of scores: one per label, in label order.
 * `labels`: [{label_id | id, name, type, settings}] (queue labels carry the
 * label's id in `label_id`; annotation labels in `id`). `values`: keyed by
 * that id. `refusals` (forms only): the entries a field still holds that its
 * rule refused, keyed by the same id (useEntryRefusals().pending()); such a
 * label's problem is the refusal, never its kept value.
 * @returns {{label_id: string, name: string, kind: "empty"|"range"|"step"|"stars"|"choice"|"length", text: string}[]}
 */
export function labelProblems(labels = [], values = {}, refusals = {}) {
  const out = [];
  (labels ?? []).forEach((label) => {
    const id = label.label_id ?? label.id;
    const typed = refusals?.[id];
    if (typed) {
      out.push({ ...typed, label_id: id, name: label.name });
      return;
    }
    const value = values?.[id];
    if (isLabelValueEmpty(label.type, value)) {
      out.push(problem(label, "empty", `${label.name} needs a score.`));
      return;
    }
    const bad = valueProblem(label, value);
    if (bad) out.push(bad);
  });
  return out;
}

/** The rule a label shows under its input, or null when it has none. */
export function ruleText(label) {
  const settings = label?.settings ?? {};
  switch (label?.type) {
    case "numeric": {
      const { min, max, step } = numericRule(settings);
      return `${formatNumber(min)} – ${formatNumber(max)} · steps of ${formatNumber(step)}`;
    }
    case "star":
      return `1 – ${starCount(settings)} stars`;
    case "categorical": {
      const options = categoricalOptions(settings);
      if (!options.length) return null;
      return `${settings.multi_choice ? "Any of" : "One of"} ${listText(options)}`;
    }
    case "thumbs_up_down":
      return THUMBS.map((t) => t.text).join(" or ");
    case "text": {
      const { min, max } = lengthRule(settings);
      if (min != null && max != null) return `${min} – ${max} characters`;
      if (min != null) return `At least ${min} characters`;
      if (max != null) return `Up to ${max} characters`;
      return null;
    }
    default:
      return null;
  }
}

/** "Fix 2 scores before submitting: Tone needs a score. Helpfulness …" */
export function problemsText(problems = []) {
  if (!problems?.length) return "";
  const n = problems.length;
  return `Fix ${n} ${n === 1 ? "score" : "scores"} before submitting: ${problems
    .map((p) => p.text)
    .join(" ")}`;
}
