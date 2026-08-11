const KEY_MAP = {
  assigned_users: "assignedUsers",
  auto_annotate: "autoAnnotate",
  can_annotate: "canAnnotate",
  cell_description: "cellDescription",
  cell_value: "cellValue",
  column_id: "columnId",
  column_name: "columnName",
  current_row_number: "currentRowNumber",
  data_type: "dataType",
  display_type: "displayType",
  field_type: "fieldType",
  first_row_order: "firstRowOrder",
  label_id: "labelId",
  label_name: "labelName",
  label_requirements: "labelRequirements",
  label_settings: "labelSettings",
  label_type: "labelType",
  last_row_order: "lastRowOrder",
  lowest_unfinished_row: "lowestUnfinishedRow",
  max_length: "maxLength",
  min_length: "minLength",
  multi_choice: "multiChoice",
  next_row_number: "nextRowNumber",
  next_row_order: "nextRowOrder",
  previous_row_number: "previousRowNumber",
  previous_row_order: "previousRowOrder",
  response_fields: "responseFields",
  row_id: "rowId",
  row_order: "rowOrder",
  static_fields: "staticFields",
  step_size: "stepSize",
  total_rows: "totalRows",
};

const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

export function normalizeAnnotationKeys(value) {
  if (Array.isArray(value)) return value.map(normalizeAnnotationKeys);
  if (!value || typeof value !== "object") return value;

  return Object.entries(value).reduce((acc, [key, rawValue]) => {
    const normalizedKey = KEY_MAP[key] || key;
    const normalizedValue = normalizeAnnotationKeys(rawValue);

    if (!hasOwn(acc, normalizedKey) || key.includes("_")) {
      acc[normalizedKey] = normalizedValue;
    }

    return acc;
  }, {});
}

export function normalizeAnnotationListRow(row) {
  const normalized = normalizeAnnotationKeys(row || {});
  const assignedUsers = Array.isArray(normalized.assignedUsers)
    ? normalized.assignedUsers
    : [];

  return {
    ...normalized,
    assignedUsers,
    lowestUnfinishedRow: normalized.lowestUnfinishedRow ?? 0,
    summary: normalized.summary || { completed: 0, total: 0 },
  };
}

export function normalizeAnnotationPreviewData(data) {
  const normalized = normalizeAnnotationKeys(data || {});

  return {
    ...normalized,
    label: Array.isArray(normalized.label) ? normalized.label : [],
    responseFields: Array.isArray(normalized.responseFields)
      ? normalized.responseFields
      : [],
    staticFields: Array.isArray(normalized.staticFields)
      ? normalized.staticFields
      : [],
    totalRows: normalized.totalRows ?? 1,
  };
}
