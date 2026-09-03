import { serializeFilterForApi } from "src/api/contracts/filter-contract";
import { ANNOTATION_COLUMN_IDS, FIELD_CATEGORY_TO_COL_TYPE } from "./common";

const registryIdForTaskRow = (row) =>
  row?.registryId || row?.property_id || undefined;

export const taskFilterColumnId = (row) =>
  row?.property === "attributes"
    ? row?.propertyId
    : row?.propertyId || row?.property;

export const taskFilterColumnType = (row, columnId) => {
  if (ANNOTATION_COLUMN_IDS.has(columnId)) return "ANNOTATION";
  return (
    row?.apiColType ||
    row?.filterConfig?.colType ||
    FIELD_CATEGORY_TO_COL_TYPE[row?.fieldCategory] ||
    (row?.property === "attributes" ? "SPAN_ATTRIBUTE" : "SYSTEM_METRIC")
  );
};

/**
 * Convert one eval-task form row into the common API filter contract.
 *
 * `propertyId` remains the native column consumed by the existing filter
 * compiler. `registryId` is the opaque Property Registry identity and is
 * emitted independently as `property_id`; it must never replace the native
 * column id.
 */
export const serializeTaskFilterRowForApi = (
  row,
  { omitColumnType = false } = {},
) => {
  const columnId = taskFilterColumnId(row);
  if (!columnId) return null;

  const filterConfig = row?.filterConfig || {};
  const columnType = taskFilterColumnType(row, columnId);
  return serializeFilterForApi({
    column_id: columnId,
    ...(registryIdForTaskRow(row)
      ? { property_id: registryIdForTaskRow(row) }
      : {}),
    filter_config: {
      filter_type: filterConfig.filterType || "text",
      filter_op: filterConfig.filterOp || "equals",
      filter_value: filterConfig.filterValue,
      ...(!omitColumnType && columnType ? { col_type: columnType } : {}),
      ...(Array.isArray(filterConfig.attributeValueTypes)
        ? { attribute_value_types: filterConfig.attributeValueTypes }
        : {}),
    },
  });
};

export const serializeTaskFilterRowsForApi = (
  rows,
  optionsForRow = () => ({}),
) =>
  (rows || []).reduce((serialized, row) => {
    try {
      const value = serializeTaskFilterRowForApi(row, optionsForRow(row));
      if (value) serialized.push(value);
    } catch {
      // Incomplete form drafts stay in UI state but never cross the API
      // boundary. Supplied property IDs on complete rows are preserved by the
      // canonical serializer above.
    }
    return serialized;
  }, []);
