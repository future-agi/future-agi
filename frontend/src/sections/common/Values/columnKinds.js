// Columns whose cells were seen holding JSON (objects / arrays) or chat,
// filled by the grid's cell renderers as they render (module-level, per
// page). The column menu reads it to offer `Column from path…` on text
// columns whose values parse as JSON.
export const jsonValueColumns = new Set();
export const chatValueColumns = new Set();

export const JSON_DATA_TYPES = new Set([
  "json",
  "conversation",
  "trace",
  "checks",
  "array",
]);

/** True when a grid column holds JSON (declared or seen). */
export const isJsonColumn = (column) =>
  JSON_DATA_TYPES.has(column?.dataType ?? column?.data_type) ||
  jsonValueColumns.has(column?.id ?? column?.field);
