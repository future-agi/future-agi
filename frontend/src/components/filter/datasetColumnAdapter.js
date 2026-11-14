/**
 * Dataset-style grid filters <-> shared Filter panel.
 *
 * Used by the Evals logs filter and the annotation-queue dataset picker. The
 * grid keeps its filters as
 *   { id, columnId, filterConfig: { filterType, filterOp, filterValue } }
 * and sends them through `transformFilter`. These helpers translate that state
 * to panel rows and back, writing the same values the per-type editors wrote:
 * text as a single string (`equals` / `not_equals` for one value, `in` /
 * `not_in` only for several), numbers parsed, booleans as "true" / "false",
 * array columns as the single string typed.
 */
import {
  DATA_TYPE_TO_PANEL_TYPE,
  panelFilterToStore,
  storeFilterToPanel,
} from "src/components/filter/storeFilterAdapter";

const FIELD_TYPE_FOR_PANEL_TYPE = {
  string: "text",
  number: "number",
  boolean: "boolean",
  date: "date",
  array: "array",
};

// What each editor offered; the panel narrows its operator list to these.
const TEXT_OPERATORS = [
  "in",
  "not_in",
  "contains",
  "not_contains",
  "starts_with",
  "ends_with",
];
const NUMBER_OPERATORS = [
  "greater_than",
  "less_than",
  "equals",
  "not_equals",
  "greater_than_or_equal",
  "less_than_or_equal",
  "between",
  "not_between",
];
const ARRAY_OPERATORS = ["contains", "not_contains"];
const OPERATORS_BY_PANEL_TYPE = {
  string: TEXT_OPERATORS,
  number: NUMBER_OPERATORS,
  date: NUMBER_OPERATORS,
  boolean: ["equals"],
  array: ARRAY_OPERATORS,
};

export const DATASET_FILTER_CATEGORY = "dataset";

/**
 * Grid column defs -> field schema for <FilterPanel fields />.
 * `describe(column)` returns `{ id, label, dataType }`; columns without an id
 * are skipped. An unknown data type is treated as text, as the old editors did.
 */
export const columnsToFields = (allColumns, describe) =>
  (Array.isArray(allColumns) ? allColumns : [])
    .map((column) => {
      const { id, label, dataType } = describe(column) || {};
      if (!id) return null;
      const panelType = DATA_TYPE_TO_PANEL_TYPE[dataType] || "string";
      return {
        id,
        label: label || id,
        type: FIELD_TYPE_FOR_PANEL_TYPE[panelType],
        category: DATASET_FILTER_CATEGORY,
        operators: OPERATORS_BY_PANEL_TYPE[panelType],
        // The rows endpoint takes one string for text and array columns.
        ...(panelType === "string" || panelType === "array"
          ? { multiple: false }
          : {}),
        panelType,
      };
    })
    .filter(Boolean);

const asString = (value) =>
  value === undefined || value === null ? "" : String(value);

export const toPanelValue = (filters, fields) => {
  const lookup = {};
  fields.forEach((field) => {
    lookup[field.id] = { panelType: field.panelType };
  });
  return (filters || [])
    .filter((filter) => filter?.columnId && lookup[filter.columnId])
    .map((filter) => {
      const row = storeFilterToPanel(filter, lookup);
      if (row.fieldType === "number") {
        return {
          ...row,
          value: Array.isArray(row.value)
            ? row.value.map(asString)
            : asString(row.value),
        };
      }
      if (row.fieldType === "array" && !Array.isArray(row.value)) {
        return { ...row, value: row.value === "" ? [] : [row.value] };
      }
      return row;
    });
};

const collapseList = (config) => {
  const { filterOp, filterValue } = config;
  // One value only: a picked list collapses to its first entry.
  if (
    (filterOp === "in" || filterOp === "not_in") &&
    Array.isArray(filterValue) &&
    filterValue.length > 0
  ) {
    return {
      ...config,
      filterOp: filterOp === "in" ? "equals" : "not_equals",
      filterValue: filterValue[0],
    };
  }
  return config;
};

export const fromPanelValue = (rows) =>
  (rows || []).map((row) => {
    const filter = panelFilterToStore(row);
    const { filterType } = filter.filterConfig;
    let config = filter.filterConfig;
    if (filterType === "text") config = collapseList(config);
    if (filterType === "boolean") {
      config = { ...config, filterValue: String(config.filterValue) };
    }
    if (
      filterType === "array" &&
      Array.isArray(config.filterValue) &&
      config.filterValue.length > 0
    ) {
      config = { ...config, filterValue: config.filterValue[0] };
    }
    return { ...filter, filterConfig: config };
  });
