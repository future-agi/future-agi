/**
 * Returns true when a column should appear in the Extract JSON Key dropdown.
 *
 * A column is eligible when:
 *   - its dataType is "json" (natively typed), OR
 *   - the schema endpoint has recorded at least one key for it
 *     (covers api_call columns whose responses contain JSON objects)
 *
 * Arrays are correctly excluded: jsonSchemas entries for array-shaped columns
 * have an empty keys array, so keys?.length is 0 / falsy.
 */
export const isJsonColumn = (column, jsonSchemas) =>
  column.dataType === "json" ||
  Boolean(jsonSchemas?.[column.field]?.keys?.length);
