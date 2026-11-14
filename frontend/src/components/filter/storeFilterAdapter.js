/**
 * Conversions between the camelCase filter store used by dataset-style grids
 *   { id, columnId, filterConfig: { filterType, filterOp, filterValue } }
 * and the shared Filter panel's rows `{ field, fieldType, operator, value }`.
 * Used by the dataset Data tab and the Evals logs filter.
 */
import { getRandomId } from "src/utils/utils";

// Dataset column data_type → panel fieldType (normalized)
export const DATA_TYPE_TO_PANEL_TYPE = {
  text: "string",
  integer: "number",
  float: "number",
  boolean: "boolean",
  datetime: "date",
  array: "array",
};

// Panel fieldType → store filterType
const PANEL_TYPE_TO_STORE_TYPE = {
  string: "text",
  number: "number",
  date: "datetime",
  boolean: "boolean",
  array: "array",
};

const formatDateInputValue = (value) => {
  if (!value) return "";
  if (value instanceof Date && !Number.isNaN(value.getTime())) {
    return value.toISOString().slice(0, 16);
  }
  const stringValue = String(value);
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(stringValue)) {
    return stringValue.slice(0, 16);
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(stringValue)) {
    return `${stringValue}T00:00`;
  }
  return stringValue;
};

const DEFAULT_OP_BY_PANEL_TYPE = {
  number: "equals",
  date: "equals",
  boolean: "equals",
  array: "contains",
  string: "in",
  text: "in",
};

// The shared TraceFilterPanel stores canonical backend operator values.
const opStoreToPanel = (storeOp, panelType) => {
  return storeOp || DEFAULT_OP_BY_PANEL_TYPE[panelType] || "equals";
};

const opPanelToStore = (panelOp, panelType) => {
  return panelOp || DEFAULT_OP_BY_PANEL_TYPE[panelType] || "equals";
};

// Store filterValue → panel value (mostly a pass-through; normalize number/date arrays)
const valueStoreToPanel = (val, panelType) => {
  if (val === undefined || val === null)
    return panelType === "number" || panelType === "date" ? "" : [];
  if (panelType === "boolean")
    return val === true || val === "true" ? "true" : "false";
  if (panelType === "date") {
    return Array.isArray(val)
      ? val.map((item) => formatDateInputValue(item))
      : formatDateInputValue(val);
  }
  return val;
};

const isNullish = (v) => v === undefined || v === null;
const valuePanelToStore = (val, panelType, operator) => {
  if (panelType === "boolean") return val === "true" || val === true;
  if (panelType === "date") {
    if (Array.isArray(val)) {
      return val.map((item) => (item ? new Date(item) : item));
    }
    return val ? new Date(val) : "";
  }
  if (Array.isArray(val)) {
    if (operator === "in" || operator === "not_in") {
      const clean = val.filter((v) => !isNullish(v) && v !== "");
      return clean.length ? clean : "";
    }
    if (panelType === "array") {
      const clean = val.filter((v) => !isNullish(v) && v !== "");
      return clean.length ? clean : "";
    }
    if (val.length === 0) return "";
    if (val.length === 1) return isNullish(val[0]) ? "" : val[0];
    if (val.every(isNullish)) return "";
    return val;
  }
  if (isNullish(val)) return "";
  // in/not_in require a list value; wrap a single typed scalar so it still applies.
  if (operator === "in" || operator === "not_in") {
    return val === "" ? "" : [val];
  }
  return val;
};

export const storeFilterToPanel = (storeFilter, columnLookup) => {
  const col = columnLookup[storeFilter.columnId];
  const panelType = col?.panelType || "string";
  const category =
    col?.originType === "evaluation" || col?.originType === "evaluation_reason"
      ? "evaluation"
      : "dataset";
  return {
    field: storeFilter.columnId,
    registryId:
      storeFilter.registryId ||
      storeFilter.propertyId ||
      storeFilter.property_id ||
      col?.registryId,
    fieldCategory: category,
    fieldType: panelType,
    operator: opStoreToPanel(storeFilter.filterConfig?.filterOp, panelType),
    value: valueStoreToPanel(storeFilter.filterConfig?.filterValue, panelType),
  };
};

// TraceFilterPanel's AI-filter path wraps every LLM-returned scalar in
// an array (`[value]`) to match the trace chip-picker contract. The
// dataset rows endpoint expects scalars for text/number/date/boolean
// columns — an array-valued `filter_value` hits `.lower()`/`float()`
// and is silently swallowed by the backend's try/except, returning
// every row unfiltered (TH-4400). Unwrap here so the store always
// holds the shape `_apply_filters` expects.
export const unwrapScalarValue = (value, fieldType, operator) => {
  if (!Array.isArray(value)) {
    if (value === undefined || value === null) return "";
    return value;
  }
  if (fieldType === "array") return value;
  if (operator === "in" || operator === "not_in") {
    const clean = value.filter((item) => !isNullish(item) && item !== "");
    return clean.length ? clean : "";
  }
  if (operator === "between" || operator === "not_between") {
    return value.every(isNullish) ? "" : value;
  }
  const first = value[0];
  return isNullish(first) ? "" : first;
};

export const panelFilterToStore = (panelFilter) => {
  const storeType = PANEL_TYPE_TO_STORE_TYPE[panelFilter.fieldType] || "text";
  const rawValue = valuePanelToStore(
    panelFilter.value,
    panelFilter.fieldType,
    panelFilter.operator,
  );
  const filterValue = unwrapScalarValue(
    rawValue,
    panelFilter.fieldType,
    panelFilter.operator,
  );
  return {
    id: getRandomId(),
    columnId: panelFilter.field,
    ...(panelFilter.registryId && { registryId: panelFilter.registryId }),
    filterConfig: {
      filterType: storeType,
      filterOp: opPanelToStore(panelFilter.operator, panelFilter.fieldType),
      filterValue,
    },
    _meta: { parentProperty: "" },
  };
};
