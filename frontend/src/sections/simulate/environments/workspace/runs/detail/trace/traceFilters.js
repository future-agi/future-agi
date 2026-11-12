import { OUTCOME_LABELS } from "./traceTable.constants";

// The Scenarios tab's filters, shared by the run's calls table and the run
// compare table so both ask the server the same question.

// Text fields the server matches by "contains"; it has no negation for them.
const TEXT_KEYS = ["scenario", "situation"];
// Persona and coverage fields the server matches by value, or `_not` value.
const ENUM_KEYS = ["persona", "age", "attack", "task"];

/**
 * The table's filter state as the server's `filters` param.
 * @param {Object} filters  `{ goal, goal_not, subGoal, subGoal_not, status, status_not, goal_outcome, callExecutionId }`,
 *   plus `scenario`, `situation` and `persona`, `age`, `attack`, `task` (each with `_not`)
 * @returns {Object}
 */
export function toServerFilters(filters = {}) {
  const next = {};
  if (filters.callExecutionId?.length)
    next.call_execution_id = filters.callExecutionId;
  if (filters.goal?.length) next.goal = filters.goal;
  if (filters.goal_not?.length) next.goal_not = filters.goal_not;
  if (filters.subGoal?.length) next.sub_goal = filters.subGoal;
  if (filters.subGoal_not?.length) next.sub_goal_not = filters.subGoal_not;
  if (filters.status?.length) next.status = filters.status;
  if (filters.status_not?.length) next.status_not = filters.status_not;
  if (filters.goal_outcome?.length) next.goal_outcome = filters.goal_outcome;
  [...TEXT_KEYS, ...ENUM_KEYS, ...ENUM_KEYS.map((k) => `${k}_not`)].forEach(
    (key) => {
      if (filters[key]?.length) next[key] = filters[key];
    },
  );
  return next;
}

const textField = (value, label) => ({
  value,
  label,
  type: "string",
  operators: ["contains"],
});

// Fields whose values the server names in `level_labels`, as the Scenarios tab does.
const LABELLED_KEYS = new Set(["attack", "task", "subGoal"]);

/**
 * The Filter panel's fields, in the Scenarios tab's order and with its labels,
 * with choices from the server's facets. An enum field shows only when its
 * facet has values, as on the Scenarios tab.
 * @param {{ goal?: Array<{value: string}>, sub_goal?: Array<{value: string}>,
 *   persona?: Array<{value: string}>, age?: Array<{value: string}>,
 *   attack?: Array<{value: string}>, task?: Array<{value: string}> }} facets
 * @param {Object<string, string>} levelLabels  the server's `level_labels`
 */
export function buildTraceFilterFields(facets = {}, levelLabels = {}) {
  const enumFields = (entries) =>
    entries
      .filter(([, facet]) => facet?.length)
      .map(([value, facet, label]) => ({
        value,
        label,
        type: "enum",
        choices: facet.map((item) => item.value),
        ...(LABELLED_KEYS.has(value) && { choiceLabels: levelLabels }),
      }));
  return [
    textField("scenario", "Scenario"),
    ...enumFields([["goal", facets.goal, "Use case"]]),
    textField("situation", "Situation"),
    ...enumFields([
      ["persona", facets.persona, "Name"],
      ["age", facets.age, "Age"],
      ["attack", facets.attack, "Attack"],
      ["task", facets.task, "Task"],
      ["subGoal", facets.sub_goal, "Sub-goal"],
    ]),
    {
      value: "status",
      label: "Status",
      type: "enum",
      choices: Object.keys(OUTCOME_LABELS),
      choiceLabels: OUTCOME_LABELS,
    },
  ];
}

/**
 * What the Filter panel applied, as the table's filter state: a list of
 * `{ field, value }` rows flattens to one value list per field.
 * @param {null|Array|Object} result
 * @returns {Object}
 */
export function panelResultToFilters(result) {
  if (!result) return {};
  if (!Array.isArray(result)) return result;
  const flat = {};
  result.forEach((r) => {
    const values = Array.isArray(r.value)
      ? r.value
      : r.value != null
        ? [r.value]
        : [];
    if (values.length) flat[r.field] = [...(flat[r.field] || []), ...values];
  });
  return flat;
}

// How many filter values are set.
export const countFilterValues = (filters) =>
  Object.values(filters).reduce((a, v) => a + (v?.length || 0), 0);

// The status chip the filters select: one only when it alone describes them.
export const statusChipOf = (filters) => {
  if (filters.status_not?.length) return "";
  if (filters.status?.length === 1) return filters.status[0];
  return filters.status?.length ? "" : "all";
};

// The filters with a status chip picked: it replaces any status filter.
export const withStatusChip = (filters, chip) => {
  const { status: _status, status_not: _statusNot, ...rest } = filters;
  return chip === "all" ? rest : { ...rest, status: [chip] };
};
