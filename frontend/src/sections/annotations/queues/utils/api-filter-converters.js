import {
  buildApiFilterFromPanelRow,
  normalizeColumnType,
  normalizeFilterType,
} from "src/api/contracts/filter-contract";
import {
  isRangeFilterOp,
  normalizeApiFilterOp,
} from "src/sections/annotations/queues/utils/filter-operators";

const COL_TYPE_TO_PANEL_CAT = {
  SPAN_ATTRIBUTE: "attribute",
  SYSTEM_METRIC: "system",
  EVAL_METRIC: "eval",
  ANNOTATION: "annotation",
};

export function formatDateInputValue(value) {
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
}

export function panelFilterToApi(panel, { includeMeta = false } = {}) {
  const apiFilter = buildApiFilterFromPanelRow({
    ...panel,
    apiColType: normalizeColumnType(panel.apiColType || panel.fieldCategory),
  });
  return includeMeta
    ? { ...apiFilter, _meta: { parentProperty: "" } }
    : apiFilter;
}

export function apiFilterToPanel(
  api,
  {
    propertiesById = {},
    defaultCategory = "system",
    dateFieldType = "date",
    formatDateValues = true,
  } = {},
) {
  const property = propertiesById[api?.column_id];
  const config = api?.filter_config || {};
  const rawOp = config.filter_op || "equals";
  const canonicalOp = normalizeApiFilterOp(rawOp);
  const rawVal = config.filter_value;
  const filterType = config.filter_type;
  const canonicalFilterType = normalizeFilterType(filterType, rawVal);
  const isNumberType =
    canonicalFilterType === "number" || property?.type === "number";
  const isRange = isRangeFilterOp(canonicalOp);
  const isDateType =
    filterType === "datetime" ||
    filterType === "date" ||
    filterType === "timestamp";
  let value;

  if (isRange && rawVal) {
    value = Array.isArray(rawVal)
      ? rawVal.map((v) =>
          isDateType && formatDateValues ? formatDateInputValue(v) : String(v),
        )
      : String(rawVal)
          .split(",")
          .map((v) =>
            isDateType && formatDateValues
              ? formatDateInputValue(v.trim())
              : v.trim(),
          );
  } else if (isDateType && formatDateValues) {
    value = rawVal ? formatDateInputValue(rawVal) : "";
  } else if (isNumberType) {
    value = rawVal != null ? String(rawVal) : "";
  } else if (canonicalFilterType === "boolean") {
    value = rawVal != null ? String(rawVal) : "true";
  } else if (canonicalFilterType === "map") {
    // The map editor accepts the canonical object directly and renders it as
    // JSON.  String(rawVal) would turn a saved map into "[object Object]",
    // making the rule impossible to edit or re-save without changing it.
    value = rawVal ?? "";
  } else if (canonicalFilterType === "array") {
    // Array attributes can contain typed scalar members.  Preserve numbers
    // and booleans instead of stringifying every member during hydration.
    value = Array.isArray(rawVal) ? rawVal : rawVal == null ? [] : [rawVal];
  } else if (Array.isArray(rawVal)) {
    value = rawVal.map((v) => String(v));
  } else {
    value = rawVal
      ? String(rawVal)
          .split(",")
          .map((v) => v.trim())
      : [];
  }

  const rawColType = config.col_type || api?.col_type;
  const fieldType = (() => {
    if (isDateType) return dateFieldType;
    if (isNumberType) return "number";
    if (canonicalFilterType === "boolean") return "boolean";
    if (canonicalFilterType === "map") return "map";
    if (canonicalFilterType === "array") return "array";
    if (filterType === "categorical") return "categorical";
    if (filterType === "text" && rawColType === "ANNOTATION") return "text";
    return property?.type || "string";
  })();

  return {
    field: api.column_id,
    ...((api.property_id || property?.registryId) && {
      registryId: api.property_id || property?.registryId,
    }),
    fieldName: api.display_name || property?.name,
    fieldCategory:
      COL_TYPE_TO_PANEL_CAT[rawColType] ||
      property?.category ||
      defaultCategory,
    fieldType,
    operator: canonicalOp,
    value,
    valueTypes: config.attribute_value_types,
  };
}
