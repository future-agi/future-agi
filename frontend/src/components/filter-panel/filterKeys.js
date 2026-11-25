const KEYED_TEXT_OPERATORS = ["contains", "not_contains", "starts_with"];

export const filterKeyOf = (row, fieldType, operatorKeys = false) => {
  if (row.operator === "is_not" || row.operator === "not_equals")
    return `${row.field}_not`;
  if (
    operatorKeys &&
    fieldType !== "enum" &&
    KEYED_TEXT_OPERATORS.includes(row.operator)
  )
    return `${row.field}__${row.operator}`;
  return row.field;
};

export const operatorOfKey = (key, fieldMap, operatorKeys) => {
  const [field, keyed] = key.split("__");
  if (operatorKeys && KEYED_TEXT_OPERATORS.includes(keyed))
    return { field, operator: keyed };
  const isNeg = key.endsWith("_not");
  const bare = isNeg ? key.slice(0, -4) : key;
  if (fieldMap[bare]?.type === "enum")
    return { field: bare, operator: isNeg ? "is_not" : "is" };
  if (operatorKeys)
    return { field: bare, operator: isNeg ? "not_equals" : "equals" };
  return { field: bare, operator: isNeg ? "is_not" : "contains" };
};
