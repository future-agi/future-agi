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
