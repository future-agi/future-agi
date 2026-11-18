/**
 * Model dataset filters <-> shared Filter panel.
 *
 * The model pages keep their filters as
 *   { id, key, dataType: "string" | "number" | "date", operator, value[] }
 * with operators `equal`, `notEqual`, `greaterThan`, `greaterThanEqualTo`,
 * `lessThan`, `lessThanEqualTo`, `between`, `notBetween`, and
 * `validateDatasetFilter` turns them into the request. Strings are a single
 * pick from the property's options ("is"), numbers keep their typed text, and
 * dates are Date objects.
 */
import { format } from "date-fns";
import { getRandomId } from "src/utils/utils";

const TO_PANEL_OPERATOR = {
  equal: "equals",
  notEqual: "not_equals",
  greaterThan: "greater_than",
  greaterThanEqualTo: "greater_than_or_equal",
  lessThan: "less_than",
  lessThanEqualTo: "less_than_or_equal",
  between: "between",
  notBetween: "not_between",
};
const FROM_PANEL_OPERATOR = Object.fromEntries(
  Object.entries(TO_PANEL_OPERATOR).map(([model, panel]) => [panel, model]),
);
const RANGE_OPERATORS = new Set(["between", "notBetween"]);
const DATE_INPUT_FORMAT = "yyyy-MM-dd'T'HH:mm";

const NUMERIC_OPERATORS = Object.values(TO_PANEL_OPERATOR);

export const blankDatasetFilter = () => ({
  id: getRandomId(),
  key: "",
  dataType: "",
  value: [],
  operator: "",
});

const asString = (value) =>
  value === undefined || value === null ? "" : String(value);

const formatDate = (value) => {
  if (!value) return "";
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? "" : format(date, DATE_INPUT_FORMAT);
};

const parseDate = (value) => {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date;
};

/** `properties` ([{ label, value, dataType }]) -> field schema. */
export const propertiesToFields = (properties = [], datasetOptions = {}) =>
  properties.map(({ label, value, dataType }) => {
    if (dataType === "number") {
      return {
        id: value,
        label,
        type: "number",
        operators: NUMERIC_OPERATORS,
      };
    }
    if (dataType === "date") {
      return {
        id: value,
        label,
        type: "date",
        operators: NUMERIC_OPERATORS,
      };
    }
    const options = datasetOptions?.[value] || [];
    return options.length
      ? { id: value, label, type: "enum", options, operators: ["equals"] }
      : { id: value, label, type: "text", operators: ["in"], multiple: false };
  });

export const toPanelValue = (filters, fields) => {
  const byId = Object.fromEntries(fields.map((field) => [field.id, field]));
  const rows = [];
  (filters || []).forEach((filter) => {
    const field = byId[filter?.key];
    if (!field || !filter.operator) return;
    const values = Array.isArray(filter.value) ? filter.value : [];
    if (!values.length || values[0] === undefined || values[0] === "") return;
    const operator = TO_PANEL_OPERATOR[filter.operator] || "equals";
    if (filter.dataType === "number") {
      rows.push({
        field: field.id,
        fieldType: "number",
        operator,
        value: RANGE_OPERATORS.has(filter.operator)
          ? [asString(values[0]), asString(values[1])]
          : asString(values[0]),
      });
    } else if (filter.dataType === "date") {
      rows.push({
        field: field.id,
        fieldType: "date",
        operator,
        value: RANGE_OPERATORS.has(filter.operator)
          ? [formatDate(values[0]), formatDate(values[1])]
          : formatDate(values[0]),
      });
    } else if (field.type === "enum") {
      rows.push({
        field: field.id,
        fieldType: "categorical",
        operator: "equals",
        value: [values[0]],
      });
    } else {
      rows.push({
        field: field.id,
        fieldType: "string",
        operator: "in",
        value: values.map(String),
      });
    }
  });
  return rows;
};

export const fromPanelValue = (rows, fields) => {
  const byId = Object.fromEntries(fields.map((field) => [field.id, field]));
  const filters = [];
  (rows || []).forEach((row) => {
    const field = byId[row?.field];
    if (!field) return;
    const operator = FROM_PANEL_OPERATOR[row.operator] || "equal";
    const base = { id: getRandomId(), key: field.id };
    if (field.type === "number") {
      const values = Array.isArray(row.value) ? row.value : [row.value];
      filters.push({
        ...base,
        dataType: "number",
        operator,
        value: RANGE_OPERATORS.has(operator)
          ? [asString(values[0]), asString(values[1])]
          : [asString(values[0])],
      });
    } else if (field.type === "date") {
      const values = Array.isArray(row.value) ? row.value : [row.value];
      filters.push({
        ...base,
        dataType: "date",
        operator,
        value: RANGE_OPERATORS.has(operator)
          ? [parseDate(values[0]), parseDate(values[1])]
          : [parseDate(values[0])],
      });
    } else {
      const values = (
        Array.isArray(row.value) ? row.value : [row.value]
      ).filter((item) => item !== undefined && item !== null && item !== "");
      if (!values.length) return;
      filters.push({
        ...base,
        dataType: "string",
        operator: "equal",
        value: [values[0]],
      });
    }
  });
  return filters;
};
