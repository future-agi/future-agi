export const newInput = (alias = "value") => ({
  alias,
  source: "span",
  field: "attribute_number",
  attribute_key: "",
  reducer: "latest",
  config_hash: "",
});

export const initialForm = () => ({
  project_id: "",
  name: "",
  description: "",
  formula: "ratio(resolved == 1 and escalated == 0, resolved >= 0)",
  inputs: [
    { ...newInput("resolved"), attribute_key: "resolved" },
    { ...newInput("escalated"), attribute_key: "escalated" },
  ],
  selection_unit: "session",
  weighting: "none",
  cohort_key: "",
  filters: [],
});

/** Do not echo server-owned provenance into the strict write contract. */
export function formFromMetric(metric) {
  const { formula, inputs, selection_unit, weighting, cohort_key, filters } =
    metric.definition;
  return {
    project_id: metric.project_id,
    name: metric.name,
    description: metric.description,
    formula,
    inputs,
    selection_unit,
    weighting,
    cohort_key,
    filters,
  };
}

/** @returns {import("../../generated/api-contracts/api.schemas").DerivedMetricWriteApi} */
export function metricRequestBody(form, metric) {
  return {
    ...form,
    inputs: form.inputs.map((input) =>
      input.source === "eval"
        ? {
            alias: input.alias,
            source: "eval",
            reducer: input.reducer,
            config_id: input.config_id,
            config_hash: input.config_hash,
          }
        : {
            alias: input.alias,
            source: "span",
            reducer: input.reducer,
            field: input.field,
            attribute_key: input.attribute_key,
          },
    ),
    ...(metric ? { expected_version: metric.current_version } : {}),
  };
}

/**
 * A weighted metric reads sampled results, which are always stored under the
 * evaluator's config_hash. An empty hash selects only legacy unversioned results,
 * so a weighted metric with an unhashed evaluator input could never find a weight.
 * The server enforces the same rule; this surfaces it before a round trip.
 * @returns {string[]} one human-readable problem per unmet requirement
 */
export function weightedInputProblems(form) {
  if (form.weighting !== "inverse_probability") return [];
  const evaluators = form.inputs.filter((input) => input.source === "eval");
  if (evaluators.length === 0)
    return [
      "Inverse-probability weighting needs at least one evaluation input.",
    ];
  return evaluators
    .filter((input) => !input.config_hash)
    .map(
      (input) =>
        `Input "${input.alias}" needs an evaluator selected (or its exact hash entered): an empty hash reads only legacy unversioned results, which are never sampled.`,
    );
}

/** Bind an evaluator picked from the project list: BOTH id and persisted hash. */
export const evaluatorBinding = (evaluator) => ({
  config_id: evaluator?.id ?? "",
  config_hash: evaluator?.config_hash ?? "",
});

/**
 * An evaluator's persisted hash follows its definition. When the bound hash is
 * set but no longer equals the listed evaluator's current hash, new results land
 * under a different hash and this input keeps reading only the older ones.
 */
export const evaluatorDrift = (input, listed) =>
  Boolean(
    listed && input.config_hash && listed.config_hash !== input.config_hash,
  );
