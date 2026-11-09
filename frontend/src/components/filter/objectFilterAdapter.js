/**
 * Object-style filters <-> shared Filter panel.
 *
 * Several screens hold their filters as a plain object of value lists,
 *   { status: ["failed"], owner_not: ["system"] }
 * with `<field>_not` for "is not", and describe their fields as
 *   { value, label, type: "enum" | "string", choices, choiceLabels, single,
 *     operators }.
 * Whichever operator the user picks, the payload only records which values are
 * included or excluded, so that is all this adapter writes. Keys that are not
 * one of the fields are passed through untouched.
 */

const POSITIVE_OPERATORS = new Set(["is", "contains", "equals", "starts_with"]);
const NEGATIVE_OPERATORS = new Set(["is_not", "not_contains", "not_equals"]);

const isNegativeOperator = (operator) =>
  operator === "not_in" ||
  operator === "not_equals" ||
  NEGATIVE_OPERATORS.has(operator);

const allows = (field, set) =>
  !Array.isArray(field.operators) ||
  field.operators.some((operator) => set.has(operator));

/** Legacy `filterFields` -> field schema for <FilterPanel fields />. */
export const objectFieldsToFields = (filterFields = []) =>
  filterFields.map((field) => {
    const positive = allows(field, POSITIVE_OPERATORS);
    const negative = allows(field, NEGATIVE_OPERATORS);
    const isEnum = field.type === "enum";
    const single = Boolean(field.single);
    const pick = (listOp, singleOp) => (single ? singleOp : listOp);
    const operators = [
      ...(positive ? [isEnum ? pick("in", "equals") : "in"] : []),
      ...(negative ? [isEnum ? pick("not_in", "not_equals") : "not_in"] : []),
    ];
    if (!isEnum) {
      return {
        id: field.value,
        label: field.label,
        type: "text",
        operators,
      };
    }
    return {
      id: field.value,
      label: field.label,
      type: "enum",
      multiple: !single,
      options: (field.choices || []).map((choice) => ({
        value: choice,
        label: field.choiceLabels?.[choice] ?? String(choice),
      })),
      operators,
    };
  });

const asList = (value) => {
  if (Array.isArray(value)) return value;
  return value ? [value] : [];
};

const splitKey = (key) =>
  key.endsWith("_not")
    ? { field: key.slice(0, -4), negative: true }
    : { field: key, negative: false };

/** Object filters -> panel rows (one row per included / excluded set). */
export const toPanelValue = (currentFilters, filterFields) => {
  if (!currentFilters || typeof currentFilters !== "object") return [];
  const fieldMap = Object.fromEntries(filterFields.map((f) => [f.value, f]));
  const entries = Array.isArray(currentFilters)
    ? Object.entries(
        currentFilters.reduce((acc, row) => {
          const negative = isNegativeOperator(row.operator);
          const key = negative ? `${row.field}_not` : row.field;
          acc[key] = [...(acc[key] || []), ...asList(row.value)];
          return acc;
        }, {}),
      )
    : Object.entries(currentFilters);

  const rows = [];
  entries.forEach(([key, value]) => {
    const { field, negative } = splitKey(key);
    const def = fieldMap[field];
    if (!def) return;
    let values = asList(value);
    if (!values.length) return;
    const isEnum = def.type === "enum";
    if (isEnum && def.single) values = values.slice(0, 1);
    rows.push({
      field,
      fieldType: isEnum && def.single ? "categorical" : "string",
      operator:
        isEnum && def.single
          ? negative
            ? "not_equals"
            : "equals"
          : negative
            ? "not_in"
            : "in",
      value: values,
    });
  });
  return rows;
};

/** Panel rows -> object filters, or null when nothing is set. */
export const fromPanelValue = (rows, filterFields, currentFilters) => {
  const fieldMap = Object.fromEntries(filterFields.map((f) => [f.value, f]));
  const result = {};

  // Keys the fields do not describe stay as the screen holds them.
  if (currentFilters && !Array.isArray(currentFilters)) {
    Object.entries(currentFilters).forEach(([key, value]) => {
      if (!fieldMap[splitKey(key).field]) result[key] = value;
    });
  }

  (rows || []).forEach((row) => {
    const def = fieldMap[row?.field];
    if (!def) return;
    const values = asList(row.value).filter(
      (value) => value !== "" && value !== undefined && value !== null,
    );
    if (!values.length) return;
    const key = isNegativeOperator(row.operator)
      ? `${row.field}_not`
      : row.field;
    const bucket = result[key] || [];
    values.forEach((value) => {
      if (!bucket.includes(value)) bucket.push(value);
    });
    result[key] = def.single ? bucket.slice(0, 1) : bucket;
  });

  return Object.keys(result).length > 0 ? result : null;
};
