// Keep the annotation queue add-items dialog on the same canonical filter
// contract as Observe. Legacy panel names are accepted only at the boundary.

export const PANEL_OP_TO_API = {
  is: "equals",
  is_not: "not_equals",
  contains: "contains",
  not_contains: "not_contains",
  starts_with: "starts_with",
  ends_with: "ends_with",
  equals: "equals",
  not_equals: "not_equals",
  equal_to: "equals",
  not_equal_to: "not_equals",
  greater_than: "greater_than",
  greater_than_or_equal: "greater_than_or_equal",
  less_than: "less_than",
  less_than_or_equal: "less_than_or_equal",
  between: "between",
  not_between: "not_between",
  not_in_between: "not_between",
  inBetween: "between",
  is_empty: "is_null",
  is_not_empty: "is_not_null",
  is_null: "is_null",
  is_not_null: "is_not_null",
  before: "less_than",
  after: "greater_than",
  on: "equals",
  in: "in",
  not_in: "not_in",
};

const API_OP_ALIASES = {
  is: "equals",
  is_not: "not_equals",
  equal_to: "equals",
  not_equal_to: "not_equals",
  not_in_between: "not_between",
  inBetween: "between",
};

const NUMBER_API_TO_PANEL = {
  equals: "equals",
  not_equals: "not_equals",
  greater_than: "greater_than",
  greater_than_or_equal: "greater_than_or_equal",
  less_than: "less_than",
  less_than_or_equal: "less_than_or_equal",
  between: "between",
  not_between: "not_between",
};

const DATE_API_TO_PANEL = {
  equals: "on",
  less_than: "before",
  greater_than: "after",
  between: "between",
  not_between: "not_between",
};

const TEXT_API_TO_PANEL = {
  equals: "is",
  not_equals: "is_not",
  contains: "contains",
  not_contains: "not_contains",
  starts_with: "starts_with",
  ends_with: "ends_with",
  in: "is",
  not_in: "is_not",
  is_null: "is_empty",
  is_not_null: "is_not_empty",
};

export const NUMBER_FILTER_OPS = new Set(Object.keys(NUMBER_API_TO_PANEL));
export const RANGE_FILTER_OPS = new Set(["between", "not_between"]);
const VALUELESS_FILTER_OPS = new Set([
  "is_null",
  "is_not_null",
  "is_empty",
  "is_not_empty",
]);

export function normalizeApiFilterOp(op) {
  if (!op) return op;
  return API_OP_ALIASES[op] || op;
}

export function panelOpToApi(op) {
  if (!op) return op;
  return PANEL_OP_TO_API[op] || normalizeApiFilterOp(op);
}

export function panelOperatorAndValueToApi(operator, value) {
  const baseOp = panelOpToApi(operator);
  let filterOp = baseOp;
  let filterValue = value;

  if (Array.isArray(filterValue)) {
    if (baseOp === "between" || baseOp === "not_between") {
      filterValue = filterValue.map(String);
    } else if (baseOp === "equals") {
      filterOp = "in";
      filterValue = filterValue.map(String);
    } else if (baseOp === "not_equals") {
      filterOp = "not_in";
      filterValue = filterValue.map(String);
    } else if (filterValue.length === 1) {
      filterValue = filterValue[0];
    } else if (filterValue.length > 1) {
      filterValue = filterValue.join(",");
    }
  }

  return { filterOp, filterValue };
}

export function apiFilterHasValue(filter) {
  const op = normalizeApiFilterOp(filter?.filterConfig?.filterOp);
  if (!filter?.columnId || !op) return false;
  if (VALUELESS_FILTER_OPS.has(op)) return true;

  const value = filter?.filterConfig?.filterValue;
  if (Array.isArray(value)) {
    return value.length > 0 && value.every((v) => v !== "" && v != null);
  }
  return value !== "" && value !== undefined && value !== null;
}

export function apiOpToPanel(op, fieldType) {
  const canonicalOp = normalizeApiFilterOp(op);

  if (fieldType === "number") {
    return NUMBER_API_TO_PANEL[canonicalOp] || canonicalOp;
  }

  if (fieldType === "date" || fieldType === "datetime") {
    return DATE_API_TO_PANEL[canonicalOp] || canonicalOp;
  }

  return TEXT_API_TO_PANEL[canonicalOp] || canonicalOp;
}

export function isNumberFilterOp(op) {
  return NUMBER_FILTER_OPS.has(normalizeApiFilterOp(op));
}

export function isRangeFilterOp(op) {
  return RANGE_FILTER_OPS.has(normalizeApiFilterOp(op));
}
