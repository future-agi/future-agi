import {
  invalidComparisonAliases,
  KIND_LABELS,
  LEVEL_LABELS,
  OPERATOR_LABELS,
} from "./builderSpec";

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

// The kinds of span attribute a custom-formula input can read by key.
const ATTRIBUTE_FIELD_LABELS = {
  attribute_number: "Number attribute",
  attribute_bool: "Yes/no attribute",
  attribute_text: "Text attribute",
};

/** A span field as a person reads it: the project catalog's display name
 * when one was returned, else the product's name for it. The wire value
 * stays the stored field. */
export function spanFieldLabel(field, catalogLabels = {}) {
  return (
    catalogLabels[field] ||
    ATTRIBUTE_FIELD_LABELS[field] ||
    BUILTIN_FIELD_LABELS[field] ||
    enumLabel({}, field)
  );
}

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

/** Shown where a comparison value has not been chosen yet; never a default. */
export const MISSING_VALUE = "…";

// An empty string is a real text value (the server compares with it), so it
// reads as "empty text", neither as missing nor as a bare "".
const literal = (value) =>
  value == null
    ? MISSING_VALUE
    : value === ""
      ? "empty text"
      : typeof value === "string"
        ? `"${value}"`
        : String(value);

const levelOf = (recipe) =>
  recipe.level ||
  (recipe.selection_unit === "session"
    ? "conversation"
    : recipe.selection_unit);

const describeInput = (input, labelFor) =>
  `${labelFor(input.alias)} (${input.condition_stage === "observation" ? `${input.reducer} observation` : input.reducer})`;

/** The comparison an input is held to, wherever the recipe keeps it. */
function inputCondition(recipe, input) {
  return input.condition_stage === "observation"
    ? input.condition
    : (recipe.field_spec?.conditions || []).find(
        (item) => item.alias === input.alias,
      );
}

/** One condition split into its parts, so a screen can mark a missing value
 * for assistive technology: {subject, operator, value, missing}. operator is
 * null when the input has no condition. */
export function conditionParts(recipe, input, labelFor = (alias) => alias) {
  const subject = describeInput(input, labelFor);
  const condition = inputCondition(recipe, input);
  if (!condition)
    return { subject, operator: null, value: null, missing: false };
  const operator = OPERATOR_LABELS[condition.operator] || condition.operator;
  if (condition.right_alias)
    return {
      subject,
      operator,
      value: labelFor(condition.right_alias),
      missing: false,
    };
  return {
    subject,
    operator,
    value: literal(condition.value),
    missing: condition.value == null,
  };
}

const clauseText = ({ subject, operator, value }) =>
  operator ? `${subject} ${operator} ${value}` : subject;

/** One condition in the same words as recipeSentence, e.g.
 * "resolved (latest) is 1"; a missing value reads "…", never a default. */
export function conditionClause(recipe, input, labelFor = (alias) => alias) {
  return clauseText(conditionParts(recipe, input, labelFor));
}

/** The recipe sentence as a lead and its condition parts, joined
 * "<lead> where <clause> and <clause>". */
export function recipeSentenceParts(recipe, labelFor = (alias) => alias) {
  const kind = recipe.kind || "custom";
  const units = LEVEL_LABELS[levelOf(recipe)] || "units";
  const inputs = recipe.inputs || [];
  if (kind === "share" || kind === "count")
    return {
      lead: kind === "share" ? `Share of ${units}` : `Number of ${units}`,
      clauses: inputs.map((input) => conditionParts(recipe, input, labelFor)),
    };
  if (kind === "average" || kind === "total") {
    const lead = kind === "average" ? "Average" : "Total";
    return {
      lead: inputs.length
        ? `${lead} of ${describeInput(inputs[0], labelFor)} across ${units}`
        : `${lead} across ${units}`,
      clauses: [],
    };
  }
  return { lead: `${KIND_LABELS[kind] || kind} across ${units}`, clauses: [] };
}

/** One plain sentence for a builder recipe, e.g.
 * "Share of conversations where resolved (latest) is 1". */
export function recipeSentence(recipe, labelFor = (alias) => alias) {
  const { lead, clauses } = recipeSentenceParts(recipe, labelFor);
  return clauses.length
    ? `${lead} where ${clauses.map(clauseText).join(" and ")}`
    : lead;
}

/** Why the live preview is waiting, naming only the piece still missing, so
 * it never asks for a project or source the person already chose. A value is
 * named by the same rule the save checks use (invalidComparisonAliases), so
 * the footer and the preview always point at the same field. */
export function previewNeeds(form, labelFor = (alias) => alias) {
  if (!form?.project_id) return "Choose a project to see a preview.";
  const inputs = form.inputs || [];
  if (!inputs.length) return "Choose a source to see a preview.";
  if ((form.kind || "custom") === "custom" && !form.formula?.trim())
    return "Enter a formula to see a preview.";
  const [alias] = invalidComparisonAliases(form);
  if (alias) return `Choose a value for ${labelFor(alias)} to see a preview.`;
  return "Finish the calculation to see a preview.";
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
