import { KIND_LABELS, LEVEL_LABELS, OPERATOR_LABELS } from "./builderSpec";

// Presentation-only helpers. They never change a recipe or a returned value;
// they only decide how an existing input or definition is named on screen.

// Compiled by the server from the typed conditions; the Result column already
// shows its outcome, so it is not a user input.
const RESERVED_ALIASES = new Set(["field_condition"]);

const BUILTIN_FIELD_LABELS = {
  latency_ms: "Duration",
  cost: "Cost",
  total_tokens: "Total tokens",
  prompt_tokens: "Prompt tokens",
  completion_tokens: "Completion tokens",
};

// Definition enums as a person would say them. Unknown values are shown as
// returned (underscores become spaces), never dropped or guessed.
export const VALUE_TYPE_LABELS = {
  number: "Number",
  boolean: "Yes or no",
  text: "Text",
};
export const VALUE_FORMAT_LABELS = { number: "Number", percent: "Percentage" };
export const WEIGHTING_LABELS = {
  none: "None",
  inverse_probability: "Inverse-probability weighted",
};
export const REDUCER_LABELS = {
  first: "First value",
  latest: "Latest value",
  min: "Lowest value",
  max: "Highest value",
  mean: "Average",
  sum: "Sum",
  count: "Count",
  any: "Any",
  all: "All",
};

/** A label from one of the tables above; null stays null for the caller. */
export function enumLabel(labels, value) {
  if (value == null || value === "") return null;
  if (Object.hasOwn(labels, value)) return labels[value];
  const text = String(value).replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Where an input reads from, e.g. "Span attribute" or "Evaluation". */
export function inputSourceKind(input) {
  if (input?.source === "eval") return "Evaluation";
  if (input?.source === "span")
    return input.attribute_key ? "Span attribute" : "Span field";
  return enumLabel({}, input?.source);
}

/** The name a person picked for an input: its source label, never its alias. */
export function inputLabel(alias, { inputs = [], sourceLabels = {} } = {}) {
  if (sourceLabels[alias]) return sourceLabels[alias];
  const input = inputs.find((item) => item.alias === alias);
  if (!input) return alias;
  if (input.attribute_key) return input.attribute_key;
  if (input.source === "span" && BUILTIN_FIELD_LABELS[input.field])
    return BUILTIN_FIELD_LABELS[input.field];
  return alias;
}

/** Returned per-unit inputs in their returned order, without reserved aliases. */
export function unitInputs(unit) {
  return Object.entries({
    ...unit?.inputs,
    ...unit?.inputs_bool,
    ...unit?.inputs_text,
  }).filter(([alias]) => !RESERVED_ALIASES.has(alias));
}

const literal = (value) =>
  value == null
    ? "…"
    : typeof value === "string"
      ? `"${value}"`
      : String(value);

const levelOf = (recipe) =>
  recipe.level ||
  (recipe.selection_unit === "session"
    ? "conversation"
    : recipe.selection_unit);

/** One plain sentence for a builder recipe, e.g.
 * "Share of conversations where resolved (latest) is 1". */
export function recipeSentence(recipe, labelFor = (alias) => alias) {
  const kind = recipe.kind || "custom";
  const units = LEVEL_LABELS[levelOf(recipe)] || "units";
  const inputs = recipe.inputs || [];
  const conditions = recipe.field_spec?.conditions || [];
  const describe = (input) =>
    `${labelFor(input.alias)} (${input.condition_stage === "observation" ? `${input.reducer} observation` : input.reducer})`;
  if (kind === "share" || kind === "count") {
    const lead = kind === "share" ? `Share of ${units}` : `Number of ${units}`;
    const clauses = inputs.map((input) => {
      const condition =
        input.condition_stage === "observation"
          ? input.condition
          : conditions.find((item) => item.alias === input.alias);
      if (!condition) return describe(input);
      const operator =
        OPERATOR_LABELS[condition.operator] || condition.operator;
      const right = condition.right_alias
        ? labelFor(condition.right_alias)
        : literal(condition.value);
      return `${describe(input)} ${operator} ${right}`;
    });
    return clauses.length ? `${lead} where ${clauses.join(" and ")}` : lead;
  }
  if (kind === "average" || kind === "total") {
    const lead = kind === "average" ? "Average" : "Total";
    return inputs.length
      ? `${lead} of ${describe(inputs[0])} across ${units}`
      : `${lead} across ${units}`;
  }
  return `${KIND_LABELS[kind] || kind} across ${units}`;
}

/** "Share of conversations" / "Average across conversations"; null when unknown. */
export function definitionSummary(definition) {
  if (!definition?.kind) return null;
  const kind =
    definition.kind === "percentile"
      ? "Percentile"
      : KIND_LABELS[definition.kind] || null;
  const units = LEVEL_LABELS[levelOf(definition)];
  if (!kind) return null;
  const joiner = ["share", "count"].includes(definition.kind) ? "of" : "across";
  return units ? `${kind} ${joiner} ${units}` : kind;
}

/** First use: nothing saved in the workspace yet, not an empty filter or page. */
export function isMetricFirstUse(definitions, pagination, scope) {
  const loading =
    definitions.readState === "loading" ||
    definitions.isLoading ||
    definitions.isFetching;
  return (
    !loading &&
    !definitions.isError &&
    (definitions.data?.results || []).length === 0 &&
    (pagination?.page ?? 0) === 0 &&
    !scope.projectId
  );
}
