import {
  COLUMN_TYPE_ALIASES,
  FIELD_TYPE_ALIASES,
  FILTER_TYPE_ALLOWED_OPS,
  LIST_FILTER_OPS,
  NO_VALUE_FILTER_OPS,
  RANGE_FILTER_OPS,
} from "./filter-contract.generated";

const LIST_OP_SET = new Set(LIST_FILTER_OPS);
const RANGE_OP_SET = new Set(RANGE_FILTER_OPS);
const NO_VALUE_OP_SET = new Set(NO_VALUE_FILTER_OPS);
const MULTI_VALUE_TYPES = new Set([
  "text",
  "categorical",
  "thumbs",
  "annotator",
]);

export const normalizeFilterType = (rawType) => {
  if (!rawType) return "text";
  const type = String(rawType).toLowerCase();
  return FIELD_TYPE_ALIASES[type] || type;
};

export const normalizeColumnType = (rawColType) => {
  if (!rawColType) return undefined;
  return COLUMN_TYPE_ALIASES[String(rawColType).toLowerCase()] || rawColType;
};

const isMultiValueCandidate = (filterType, value) =>
  Array.isArray(value) && value.length > 1 && MULTI_VALUE_TYPES.has(filterType);

export const normalizeFilterOperator = (
  operator,
  { filterType, value } = {},
) => {
  const canonicalType = normalizeFilterType(filterType);
  let op = operator || "equals";

  if (
    isMultiValueCandidate(canonicalType, value) &&
    (op === "equals" || op === "not_equals")
  ) {
    op = op === "equals" ? "in" : "not_in";
  }

  return op;
};

export const isAllowedFilterOperator = (filterType, operator) => {
  const canonicalType = normalizeFilterType(filterType);
  return Boolean(FILTER_TYPE_ALLOWED_OPS[canonicalType]?.includes(operator));
};

export const coerceFilterValue = (value, filterOp, filterType) => {
  const canonicalType = normalizeFilterType(filterType);
  if (NO_VALUE_OP_SET.has(filterOp)) return null;

  if (LIST_OP_SET.has(filterOp)) {
    const values = Array.isArray(value) ? value : [value];
    return values.filter(
      (item) => item !== "" && item !== null && item !== undefined,
    );
  }

  if (RANGE_OP_SET.has(filterOp)) {
    const values = Array.isArray(value) ? value : [value];
    return values.slice(0, 2).map((item) => {
      if (canonicalType !== "number" || item === "" || item === null)
        return item;
      const numeric = Number(item);
      return Number.isNaN(numeric) ? item : numeric;
    });
  }

  let scalar = Array.isArray(value) ? value[0] : value;
  if (
    canonicalType === "number" &&
    scalar !== "" &&
    scalar !== null &&
    scalar !== undefined
  ) {
    const numeric = Number(scalar);
    if (!Number.isNaN(numeric)) scalar = numeric;
  }
  if (canonicalType === "boolean") {
    if (scalar === "true") return true;
    if (scalar === "false") return false;
  }
  return scalar;
};

export const buildApiFilterFromPanelRow = (row) => {
  const filterType = normalizeFilterType(row?.fieldType);
  const filterOp = normalizeFilterOperator(row?.operator, {
    filterType,
    value: row?.value,
  });
  const filterValue = coerceFilterValue(row?.value, filterOp, filterType);
  const apiColType = normalizeColumnType(row?.apiColType || row?.fieldCategory);

  if (!isAllowedFilterOperator(filterType, filterOp)) {
    throw new Error(
      `Unsupported filter operator "${filterOp}" for type "${filterType}".`,
    );
  }

  return {
    column_id: row?.field,
    ...(row?.fieldName && { display_name: row.fieldName }),
    filter_config: {
      filter_type: filterType,
      filter_op: filterOp,
      filter_value: filterValue,
      ...(apiColType && { col_type: apiColType }),
    },
  };
};
