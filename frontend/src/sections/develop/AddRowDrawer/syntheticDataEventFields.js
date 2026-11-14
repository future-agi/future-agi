// Column types offered by the synthetic data column form.
const COLUMN_TYPES = new Set([
  "text",
  "boolean",
  "integer",
  "float",
  "json",
  "array",
  "datetime",
  "image",
]);

// Built-in column properties; properties the user adds are reported as
// "custom".
const PROPERTY_TYPES = new Set(["min_length", "max_length", "value"]);

const columnType = (column) => {
  const type = column?.data_type ?? column?.dataType;
  if (!type) return undefined;
  return COLUMN_TYPES.has(type) ? type : "other";
};

// A column's property names, from either the request shape ({ name: value })
// or the form shape ([{ type, value }]).
const propertyNames = (column) => {
  const property = column?.property;
  if (Array.isArray(property)) {
    return property.map((item) => item?.type).filter(Boolean);
  }
  if (property && typeof property === "object") return Object.keys(property);
  return [];
};

const propertyTypes = (column) =>
  propertyNames(column).map((name) =>
    PROPERTY_TYPES.has(name) ? name : "custom",
  );

const hasText = (value) => typeof value === "string" && value.trim() !== "";

/**
 * Properties for the synthetic dataset create and edit events, built from the
 * request payload: row and column counts, column and property types, the
 * knowledge base id, and flags for the optional settings. Dataset and column
 * names, descriptions, instructions and property values are not included.
 */
export const syntheticDataEventFields = (
  payload,
  { editMode = false, datasetId } = {},
) => {
  const columns = Array.isArray(payload?.columns) ? payload.columns : [];
  const allPropertyTypes = columns.flatMap(propertyTypes);
  const knowledgeBaseId = payload?.kb_id || undefined;
  const fields = {
    row_count: Number(payload?.num_rows) || 0,
    column_count: columns.length,
    column_types: columns.map(columnType).filter(Boolean),
    property_count: allPropertyTypes.length,
    property_types: [...new Set(allPropertyTypes)],
    ...(knowledgeBaseId && { knowledge_base_id: knowledgeBaseId }),
    has_knowledge_base: Boolean(knowledgeBaseId),
    has_use_case: hasText(payload?.dataset?.objective),
    has_patterns: hasText(payload?.dataset?.patterns),
    edit_mode: Boolean(editMode),
  };
  if (datasetId) fields.dataset_id = datasetId;
  return fields;
};

/**
 * Per-column properties for the synthetic dataset column event: the column
 * type and its property types.
 */
export const syntheticDataColumnEventFields = (columns) =>
  (Array.isArray(columns) ? columns : []).map((column) => ({
    Column_Type: columnType(column),
    Property_Types: propertyTypes(column),
  }));
