import {
  FILTER_TYPE_ALLOWED_OPS,
  NO_VALUE_FILTER_OPS,
  RANGE_FILTER_OPS as CONTRACT_RANGE_FILTER_OPS,
} from "src/api/contracts/filter-contract.generated";

export const NUMBER_FILTER_OPS = new Set(FILTER_TYPE_ALLOWED_OPS.number);
export const RANGE_FILTER_OPS = new Set(CONTRACT_RANGE_FILTER_OPS);
const VALUELESS_FILTER_OPS = new Set(NO_VALUE_FILTER_OPS);

export function normalizeApiFilterOp(op) {
  return op || "";
}

export function apiFilterHasValue(filter) {
  const op = normalizeApiFilterOp(filter?.filter_config?.filter_op);
  if (!filter?.column_id || !op) return false;
  if (VALUELESS_FILTER_OPS.has(op)) return true;

  const value = filter?.filter_config?.filter_value;
  if (Array.isArray(value)) {
    return value.length > 0 && value.every((v) => v !== "" && v != null);
  }
  return value !== "" && value !== undefined && value !== null;
}

export function isNumberFilterOp(op) {
  return NUMBER_FILTER_OPS.has(normalizeApiFilterOp(op));
}

export function isRangeFilterOp(op) {
  return RANGE_FILTER_OPS.has(normalizeApiFilterOp(op));
}
