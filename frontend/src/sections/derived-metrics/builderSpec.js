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
export function builderProblems(form, capabilities, { metadata = true } = {}) {
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
      problems.push(`Unsupported reducer for ${input.alias}.`);
    if (
      input.source === "span"
        ? !input.field ||
          (input.field.startsWith("attribute_") && !input.attribute_key)
        : input.source !== "eval" || !input.config_id
    )
      problems.push(`Choose a source for ${input.alias}.`);
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
      const comparisonType = observation ? type : reducedType(input);
      const allowed =
        sourceCapability(capabilities, comparisonType)?.operators || [];
      if (!allowed.includes(condition.operator))
        problems.push(`Unsupported comparison for ${input.alias}.`);
      if ("right_alias" in condition) {
        const right = form.inputs.find(
          (item) => item.alias === condition.right_alias,
        );
        if (!right || reducedType(right) !== comparisonType)
          problems.push("Compared sources must have the same reduced type.");
      } else {
        const value = condition.value;
        const valid =
          comparisonType === "number"
            ? typeof value === "number" &&
              Number.isFinite(value) &&
              Math.abs(value) <= 1e15
            : comparisonType === "boolean"
              ? typeof value === "boolean"
              : typeof value === "string" && value.length <= 2048;
        if (!valid)
          problems.push(
            `Choose a ${comparisonType} comparison value for ${input.alias}.`,
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
