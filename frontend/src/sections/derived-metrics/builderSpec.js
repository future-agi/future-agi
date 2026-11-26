// The builder edits the actual v2 recipe, not a second formula language. Pick
// only writable keys; leave the selected definition's semantics byte-for-byte.
const recipeKeys = [
  "formula",
  "inputs",
  "selection_unit",
  "kind",
  "level",
  "user_rule",
  "field_spec",
  "group_dims",
  "value_type",
  "unit",
  "value_format",
  "value_labels",
  "sampling",
  "weighting",
  "cohort_key",
  "filters",
];
export const DEFAULT_LABELS = { match: "Counts", other: "Doesn't count" };
export const KIND_LABELS = {
  share: "Share",
  average: "Average",
  total: "Total",
  count: "Count",
  custom: "Custom formula",
};
export const LEVEL_LABELS = {
  conversation: "conversations",
  span: "spans",
  trace: "traces",
  user: "users",
};
export const OPERATOR_LABELS = {
  eq: "is",
  ne: "is not",
  lt: "is below",
  lte: "is at most",
  gt: "is above",
  gte: "is at least",
};
// A custom formula the server compiles: one ratio, mean, sum or count_if
// call at the top level, over the default first input's name.
export const FORMULA_EXAMPLE = "e.g. ratio(value_1 > 0, value_1 >= 0)";
export const isBooleanMetric = (recipe) =>
  ["share", "count"].includes(recipe.kind);
export const sourceCapability = (capabilities, type) =>
  capabilities?.sources?.find((source) => source.value_type === type);
export const reducedType = (input) =>
  input.reducer === "count"
    ? "number"
    : input.condition_stage === "observation"
      ? "boolean"
      : input.value_type || "number";

export function previewDefinition(form) {
  return Object.fromEntries(
    recipeKeys
      .filter((key) => form[key] !== undefined)
      .map((key) => [key, form[key]]),
  );
}
export function builderWrite(form, metric) {
  return {
    project_id: form.project_id,
    name: form.name,
    description: form.description,
    ...previewDefinition(form),
    ...(metric ? { expected_version: metric.current_version } : {}),
  };
}
export function builderFromMetric(metric) {
  const definition = metric.draft_definition || metric.definition;
  if (!definition) throw new Error("The saved definition is unavailable.");
  return {
    project_id: metric.project_id,
    name: metric.name,
    description: metric.description || "",
    ...previewDefinition(definition),
  };
}
export function newBuilder(projectId = "") {
  return {
    project_id: projectId,
    name: "",
    description: "",
    formula: "",
    kind: "share",
    level: "conversation",
    selection_unit: "session",
    weighting: "none",
    filters: [],
    inputs: [],
    field_spec: { inputs: [], conditions: [] },
    value_labels: {},
    value_format: "percent",
  };
}
export function needsRebuild(metric) {
  const definition = metric?.draft_definition || metric?.definition;
  return Boolean(
    definition?.needs_rebuild ||
      (definition?.selection_unit === "user" &&
        (definition.level || "conversation") === "conversation" &&
        (definition.kind || "custom") === "custom" &&
        !definition.user_rule),
  );
}
export function rebuildAsUser(form) {
  // Deliberate new recipe. Never reinterpret the old formula as a user metric.
  return {
    ...newBuilder(form.project_id),
    name: form.name,
    description: form.description,
    level: "user",
    selection_unit: "user",
    user_rule: "any",
  };
}
export function setRecipeInputs(
  form,
  inputs,
  conditions = form.field_spec?.conditions || [],
) {
  return {
    ...form,
    inputs,
    field_spec: {
      ...form.field_spec,
      inputs,
      value_alias: inputs[0]?.alias,
      conditions,
    },
  };
}
export function changeKind(form, kind) {
  if (kind === "custom")
    return {
      ...form,
      kind,
      formula: form.formula || `mean(${form.inputs[0]?.alias || "value"})`,
      value_labels: {},
      value_format: "number",
    };
  return {
    ...newBuilder(form.project_id),
    name: form.name,
    description: form.description,
    kind,
    value_format: kind === "share" ? "percent" : "number",
    level: form.level,
    selection_unit: form.selection_unit,
    ...(form.level === "user"
      ? { user_rule: kind === "average" ? "average" : "any" }
      : {}),
  };
}
export function supportedSourceOptions(
  catalog,
  evaluators,
  capabilities,
  projectName = "this project",
) {
  const fields = (catalog?.result?.metrics || []).flatMap((item) => {
    const type = item.type === "string" ? "text" : item.type;
    if (!sourceCapability(capabilities, type)) return [];
    const custom = item.property_kind === "custom_attribute";
    const builtin =
      item.property_kind === "system_attribute" &&
      item.source === "spans" &&
      [
        "latency_ms",
        "cost",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
      ].includes(item.name);
    if (!custom && !builtin) return []; // Never turn metric_field into raw telemetry.
    if (item.attribute_types?.length > 1) return []; // Ambiguous observed types.
    return [
      {
        id: item.property_id,
        label: item.display_name || item.name,
        group: `Span attributes in ${projectName}`,
        property: item,
        descriptor: {
          source: "span",
          field: custom
            ? {
                number: "attribute_number",
                boolean: "attribute_bool",
                text: "attribute_text",
              }[type]
            : item.name,
          ...(custom ? { attribute_key: item.name } : {}),
          value_type: type,
          ordering: "sentence",
        },
      },
    ];
  });
  // The evaluator endpoint returns bindings, not output-type metadata. Start
  // with the numeric read channel and expose an explicit supported output-type
  // control in the sentence editor. Do not claim this is an inferred schema.
  // Retain both ID and exact hash when selecting a saved evaluator.
  if (sourceCapability(capabilities, "number")) {
    fields.push(
      ...(evaluators?.results || []).map((item) => ({
        id: `eval:${item.id}`,
        label: item.name || item.id,
        group: "Saved evaluations",
        descriptor: {
          source: "eval",
          config_id: item.id,
          config_hash: item.config_hash,
          value_type: "number",
          ordering: "sentence",
        },
      })),
    );
  }
  return fields;
}
export function sourceMatches(input, option) {
  const candidate = option.descriptor;
  return (
    input?.source === candidate.source &&
    (input.source === "eval"
      ? input.config_id === candidate.config_id
      : input.field === candidate.field &&
        (input.attribute_key || "") === (candidate.attribute_key || ""))
  );
}
export function selectSource(form, index, option, capabilities) {
  const inputs = [...form.inputs];
  let alias = inputs[index]?.alias;
  if (!alias) {
    let suffix = index + 1;
    while (inputs.some((input) => input.alias === `value_${suffix}`))
      suffix += 1;
    alias = `value_${suffix}`;
  }
  const capability = sourceCapability(
    capabilities,
    option.descriptor.value_type,
  );
  const reducer = capability.reducers.includes("latest")
    ? "latest"
    : capability.reducers[0];
  inputs[index] = { ...option.descriptor, alias, reducer };
  const conditions = (form.field_spec?.conditions || []).filter(
    (condition) => condition.alias !== alias,
  );
  if (isBooleanMetric(form))
    conditions.push({
      alias,
      operator: "eq",
      value: option.descriptor.value_type === "boolean" ? true : null,
    });
  return setRecipeInputs(form, inputs, conditions);
}
/** Whether a literal comparison value is usable for its compared type. The
 * one rule the save checks and the preview's idle copy share. An empty string
 * is a real text value. */
export function comparisonValueValid(value, type) {
  if (type === "number")
    return (
      typeof value === "number" &&
      Number.isFinite(value) &&
      Math.abs(value) <= 1e15
    );
  if (type === "boolean") return typeof value === "boolean";
  return typeof value === "string" && value.length <= 2048;
}

/** The type a condition on this input compares: the raw observation type, or
 * the reduced type after aggregation. */
const comparedType = (input) =>
  input.condition_stage === "observation"
    ? input.value_type || "number"
    : reducedType(input);

/** Aliases, in form order, whose literal comparison value is not usable yet. */
export function invalidComparisonAliases(form) {
  const conditions = form.field_spec?.conditions || [];
  return (form.inputs || []).flatMap((input) => {
    const comparisons =
      input.condition_stage === "observation"
        ? [input.condition]
        : conditions.filter((condition) => condition.alias === input.alias);
    return comparisons.some(
      (condition) =>
        condition &&
        !("right_alias" in condition) &&
        !comparisonValueValid(condition.value, comparedType(input)),
    )
      ? [input.alias]
      : [];
  });
}

// labelFor names an input in messages; it defaults to the alias so callers
// without source labels read exactly as before.
export function builderProblems(
  form,
  capabilities,
  { metadata = true, labelFor = (alias) => alias } = {},
) {
  const problems = [];
  if (metadata && !form.name?.trim())
    problems.push("Name your metric before saving.");
  if (!form.project_id) problems.push("Choose a project.");
  if (!capabilities)
    return [...problems, "Source capabilities are unavailable."];
  const kind = form.kind || "custom";
  const level =
    form.level ||
    (form.selection_unit === "session" ? "conversation" : form.selection_unit);
  if (
    !capabilities.kinds.includes(kind) ||
    !capabilities.levels.includes(level)
  )
    problems.push("This calculation or unit is not enabled.");
  if (
    level === "user" &&
    !capabilities.user_rules?.[kind]?.includes(form.user_rule)
  )
    problems.push("Choose a supported user rule.");
  if (!form.inputs?.length || form.inputs.length > 16)
    problems.push("Choose between one and sixteen sources.");
  if (kind === "custom" && !form.formula?.trim())
    problems.push("Enter a custom formula.");
  const conditions = form.field_spec?.conditions || [];
  for (const input of form.inputs || []) {
    const type = input.value_type || "number";
    const capability = sourceCapability(capabilities, type);
    const observation = input.condition_stage === "observation";
    if (
      !capability ||
      !(
        observation ? capability.observation_reducers : capability.reducers
      ).includes(input.reducer)
    )
      problems.push(`Unsupported reducer for ${labelFor(input.alias)}.`);
    if (
      input.source === "span"
        ? !input.field ||
          (input.field.startsWith("attribute_") && !input.attribute_key)
        : input.source !== "eval" || !input.config_id
    )
      problems.push(`Choose a source for ${labelFor(input.alias)}.`);
    if (
      observation &&
      (!input.condition ||
        "right_alias" in input.condition ||
        "alias" in input.condition)
    )
      problems.push("Observation conditions must use a single source.");
    const comparisons = observation
      ? [input.condition]
      : conditions.filter((condition) => condition.alias === input.alias);
    for (const condition of comparisons.filter(Boolean)) {
      const comparisonType = comparedType(input);
      const allowed =
        sourceCapability(capabilities, comparisonType)?.operators || [];
      if (!allowed.includes(condition.operator))
        problems.push(`Unsupported comparison for ${labelFor(input.alias)}.`);
      if ("right_alias" in condition) {
        const right = form.inputs.find(
          (item) => item.alias === condition.right_alias,
        );
        if (!right || reducedType(right) !== comparisonType)
          problems.push("Compared sources must have the same reduced type.");
      } else {
        if (!comparisonValueValid(condition.value, comparisonType))
          problems.push(
            `Choose a ${comparisonType} comparison value for ${labelFor(input.alias)}.`,
          );
      }
    }
  }
  if (
    ["share", "count"].includes(kind) &&
    !conditions.length &&
    form.inputs.some((input) => reducedType(input) !== "boolean")
  )
    problems.push("Add a condition for this source.");
  if (
    ["average", "total"].includes(kind) &&
    (conditions.length ||
      form.inputs.some((input) => reducedType(input) !== "number"))
  )
    problems.push(
      "Average and Total need a numeric source without conditions.",
    );
  if (isBooleanMetric(form)) {
    const labels = { ...DEFAULT_LABELS, ...form.value_labels };
    if (
      Object.values(labels).some(
        (value) => !value.trim() || value.length > 80,
      ) ||
      labels.match === labels.other
    )
      problems.push("Use distinct match labels of 1–80 characters.");
  }
  return [...new Set(problems)];
}
