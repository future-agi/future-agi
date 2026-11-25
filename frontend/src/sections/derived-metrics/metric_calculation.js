import { newInput } from "./metricForm";

export const calculations = [
  { value: "mean", label: "Average" },
  { value: "sum", label: "Total" },
  { value: "count_if", label: "Count matching" },
  { value: "ratio", label: "Ratio" },
  { value: "custom", label: "Custom formula" },
];

/** Recognize only the exact small subset the controls can express. Never rewrite on load. */
export function calculationFromForm(form) {
  const fallback = { kind: "custom", operator: ">=", threshold: "0" };
  const aliases = form.inputs.map((input) => input.alias);
  const simple = form.formula.match(
    /^\s*(mean|sum)\(\s*([a-zA-Z_][a-zA-Z_0-9]*)\s*\)\s*$/,
  );
  if (aliases.length === 1 && simple?.[2] === aliases[0])
    return { ...fallback, kind: simple[1] };
  const count = form.formula.match(
    /^\s*count_if\(\s*([a-zA-Z_][a-zA-Z_0-9]*)\s*(>=|<=|==|!=|>|<)\s*(-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)\s*\)\s*$/i,
  );
  if (aliases.length === 1 && count?.[1] === aliases[0])
    return { kind: "count_if", operator: count[2], threshold: count[3] };
  const ratio = form.formula.match(
    /^\s*ratio\(\s*([a-zA-Z_][a-zA-Z_0-9]*)\s*,\s*([a-zA-Z_][a-zA-Z_0-9]*)\s*\)\s*$/,
  );
  if (
    aliases.length === 2 &&
    ratio?.[1] === aliases[0] &&
    ratio?.[2] === aliases[1]
  )
    return { ...fallback, kind: "ratio" };
  return fallback;
}

/** Only explicit calculation changes generate DSL. Existing extra inputs are never discarded. */
export function applyCalculation(form, calculation) {
  const { kind, operator, threshold } = calculation;
  if (kind === "custom") return form;
  const inputs = [...form.inputs];
  if (kind === "ratio" && inputs.length === 1) {
    let alias = "denominator";
    while (inputs.some((input) => input.alias === alias)) alias += "_value";
    inputs.push({ ...newInput(alias), field: "" });
  }
  const value = inputs[0].alias;
  const formula =
    kind === "ratio"
      ? `ratio(${value}, ${inputs[1].alias})`
      : kind === "count_if"
        ? `count_if(${value} ${operator} ${threshold})`
        : `${kind}(${value})`;
  return { ...form, inputs, formula };
}

export function plainDefinitionSummary(form, evaluatorNames = {}) {
  const calculation = calculationFromForm(form);
  const describe = (input) => {
    const source =
      input.source === "eval"
        ? evaluatorNames[input.config_id] || "saved evaluation"
        : input.attribute_key ||
          (input.field || "selected").replaceAll("_", " ");
    const reduction =
      { latest: "latest", mean: "average", min: "lowest", max: "highest" }[
        input.reducer
      ] || input.reducer;
    return `the ${reduction} ${source} value per ${form.selection_unit}`;
  };
  let sentence;
  if (calculation.kind === "mean" || calculation.kind === "sum")
    sentence = `${calculation.kind === "mean" ? "Average" : "Total"} of ${describe(form.inputs[0])}.`;
  else if (calculation.kind === "ratio")
    sentence = `Total of ${describe(form.inputs[0])} divided by total of ${describe(form.inputs[1])}.`;
  else if (calculation.kind === "count_if")
    sentence = `Count ${form.selection_unit}s where ${describe(form.inputs[0])} ${calculation.operator} ${calculation.threshold}.`;
  else
    sentence = `Custom calculation across the saved per-${form.selection_unit} values.`;
  return `${sentence}${form.weighting === "inverse_probability" ? " Inverse-probability weighted." : ""}${form.filters?.length ? " With definition filters." : ""}${form.cohort_key ? " Includes cohort breakdowns." : ""}`;
}

export function definitionSummary(form, evaluatorNames = {}) {
  const reduction = form.inputs
    .map(
      (input, i) =>
        `Input ${i + 1}: ${input.source === "eval" ? `${input.config_id ? evaluatorNames[input.config_id] || "Saved evaluator (name unavailable; binding preserved)" : "Select an evaluator"} (${input.config_hash ? "exact definition" : "legacy unversioned"})` : `${(input.field || "Select field").replaceAll("_", " ")}${input.attribute_key ? `: ${input.attribute_key}` : ""}`} · ${input.reducer}`,
    )
    .join(" · ");
  const filters = (form.filters || [])
    .map((filter) => `${filter.field} = ${filter.value}`)
    .join(" · ");
  return `${form.inputs.length} input${form.inputs.length === 1 ? "" : "s"} · ${reduction} · ${form.weighting === "none" ? "Unweighted" : "Inverse-probability weighted"}${form.cohort_key ? ` · Cohort: ${form.cohort_key}` : ""} · ${filters ? `Definition filters: ${filters}` : "No definition filters"}`;
}
