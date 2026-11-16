/**
 * Field schema for the shared Filter panel.
 *
 *   fields = [{ id, label, type, options?, operators?, category? }]
 *   type   = "text" | "number" | "enum" | "date" | "boolean" | "array"
 *            (`enum` with `multiple: true` lets the user pick several options)
 *   options   enum / array choices: strings or { value, label }
 *   operators optional allow-list of canonical operator names for the field
 *
 * The panel itself (TraceFilterPanel) works on "properties"; this file turns
 * the schema into them and counts the active rows for the trigger button.
 */

const PANEL_TYPE = {
  text: "string",
  string: "string",
  number: "number",
  date: "date",
  boolean: "boolean",
  // Fixed choices use the panel's categorical type: single-select values with
  // "is" / "is not" operators.
  enum: "categorical",
  array: "array",
};

const CATEGORY_ICONS = {
  all: "solar:widget-4-linear",
  default: "solar:tuning-2-linear",
};

export const SHARED_FILTER_SOURCE = "filter";

export const fieldsToProperties = (fields = []) =>
  fields.map((field) => ({
    id: field.id,
    name: field.label,
    category: field.category || "system",
    // `enum` + `multiple` is a pick-several list: the panel's list operators
    // ("is one of") instead of single-select "is".
    // A row whose type the user picks (text / number / boolean) starts as text.
    type: field.typeSelectable
      ? "text"
      : field.type === "enum" && field.multiple
        ? "string"
        : PANEL_TYPE[field.type] || "string",
    ...(Array.isArray(field.options) && field.options.length
      ? { choices: field.options }
      : {}),
    ...(Array.isArray(field.operators) && field.operators.length
      ? { operators: field.operators }
      : {}),
    // `multiple: false` keeps the field to one value (the query takes a scalar).
    ...(field.multiple === false ? { singleValue: true } : {}),
    ...(field.typeSelectable ? { typeSelectable: true } : {}),
    ...(field.registryId ? { registryId: field.registryId } : {}),
    ...(field.apiColType ? { apiColType: field.apiColType } : {}),
    ...(field.attributeTypes ? { attributeTypes: field.attributeTypes } : {}),
    ...(field.attributeTypesExact !== undefined
      ? { attributeTypesExact: field.attributeTypesExact }
      : {}),
  }));

/**
 * Category sidebar for the property picker. Fields in a single category get no
 * sidebar; several categories get one entry each (label from `categoryLabels`).
 */
export const fieldsToCategories = (fields = [], categoryLabels = {}) => {
  const keys = [];
  fields.forEach((field) => {
    const key = field.category || "system";
    if (!keys.includes(key)) keys.push(key);
  });
  if (keys.length < 2) return [];
  return [
    { key: "all", label: "All", icon: CATEGORY_ICONS.all },
    ...keys.map((key) => ({
      key,
      label: categoryLabels[key] || key,
      icon: CATEGORY_ICONS.default,
    })),
  ];
};

export const countActiveFilters = (value) =>
  Array.isArray(value) ? value.length : 0;
