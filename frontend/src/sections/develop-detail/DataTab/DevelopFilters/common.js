import { format, isValid } from "date-fns";
import { buildApiFilterFromPanelRow } from "src/api/contracts/filter-contract";
import {
  LIST_FILTER_OPS,
  NO_VALUE_FILTER_OPS,
} from "src/api/contracts/filter-contract.generated";

export const DefaultFilter = {
  columnId: "",
  filterConfig: {
    filterType: "text",
    filterOp: "equals",
    filterValue: "",
  },
};

export const MapColumnTypeToFilterType = {
  text: "text",
  boolean: "boolean",
  integer: "number",
  float: "number",
  datetime: "datetime",
  array: "array",
  audio: "number",
};
// Filter looks like this a valid filter should have columnId string length > 0 &
// filterConfig.filterValue, filterConfig.filterOp, filterConfig.filterType should not be undefined
// if filterOp is between or not_between then filterConfig.filterValue should be an array of 2 elements
export const validateFilter = (filter) => {
  const filterValue = filter.filterConfig.filterValue;
  const filterOp = filter.filterConfig.filterOp;
  const filterType = filter.filterConfig.filterType;
  const requiresNoValue = NO_VALUE_FILTER_OPS.includes(filterOp);
  const requiresListValue = LIST_FILTER_OPS.includes(filterOp);
  const hasValue =
    requiresNoValue ||
    (requiresListValue
      ? Array.isArray(filterValue) && filterValue.length > 0
      : filterValue !== "");

  return (
    filter.columnId.length > 0 &&
    hasValue &&
    filterOp !== "" &&
    filterType !== "" &&
    (filterType === "datetime" && !requiresNoValue
      ? filterOp === "between" || filterOp === "not_between"
        ? isValid(filterValue[0]) && isValid(filterValue[1])
        : isValid(filterValue)
      : filterOp === "between" || filterOp === "not_between"
        ? Array.isArray(filterValue) && filterValue.length === 2
        : true)
  );
};

const transformFilterValue = (filterValue, filterType) => {
  if (filterType === "datetime") {
    if (Array.isArray(filterValue)) {
      return [
        filterValue[0]
          ? format(new Date(filterValue[0]), "yyyy-MM-dd HH:mm:ss")
          : filterValue[0],
        filterValue[1]
          ? format(new Date(filterValue[1]), "yyyy-MM-dd HH:mm:ss")
          : filterValue[1],
      ];
    }
    return format(new Date(filterValue), "yyyy-MM-dd HH:mm:ss");
  }
  if (filterType === "number") {
    if (Array.isArray(filterValue)) {
      return [
        filterValue[0] !== undefined
          ? parseFloat(filterValue[0])
          : filterValue[0],
        filterValue[1] !== undefined
          ? parseFloat(filterValue[1])
          : filterValue[1],
      ];
    }
    return parseFloat(filterValue);
  }
  return filterValue;
};

// FA Path filters (R2 P6): a path field is `<columnId>::<path>`; the API
// filter keeps the column id and carries the path in `filter_config.path`
// (additive; contract rule 2).
export const PATH_FIELD_SEPARATOR = "::";

/** `{columnId, path}` of a filter field (`path` empty for plain columns). */
export const splitPathField = (field) => {
  const s = String(field ?? "");
  const at = s.indexOf(PATH_FIELD_SEPARATOR);
  return at < 0
    ? { columnId: s, path: "" }
    : {
        columnId: s.slice(0, at),
        path: s.slice(at + PATH_FIELD_SEPARATOR.length),
      };
};

export const transformFilter = (filter) => {
  const { columnId, path } = splitPathField(filter.columnId);
  const built = buildApiFilterFromPanelRow({
    field: columnId,
    registryId: path
      ? undefined
      : filter.registryId || filter.propertyId || filter.property_id,
    fieldType: filter.filterConfig.filterType,
    operator: filter.filterConfig.filterOp,
    value: NO_VALUE_FILTER_OPS.includes(filter.filterConfig.filterOp)
      ? null
      : transformFilterValue(
          filter.filterConfig.filterValue,
          filter.filterConfig.filterType,
        ),
  });
  return path
    ? { ...built, filter_config: { ...built.filter_config, path } }
    : built;
};

export const compareFilterChange = (prevFilters, filters) => {
  if (!prevFilters || !filters) return false;

  const validPrevFilters = prevFilters.filter(validateFilter);
  const validFilters = filters.filter(validateFilter);

  const prevHash = JSON.stringify(validPrevFilters.map(transformFilter));

  const currentHash = JSON.stringify(validFilters.map(transformFilter));

  return prevHash === currentHash;
};
