import { format } from "date-fns";
import { buildApiFilterFromPanelRow } from "src/api/contracts/filter-contract";

export const DefaultFilter = {
  columnId: "",
  filterConfig: {
    filterType: "",
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
};
// Filter looks like this a valid filter should have columnId string length > 0 &
// filterConfig.filterValue, filterConfig.filterOp, filterConfig.filterType should not be undefined
// if filterOp is between or not_between then filterConfig.filterValue should be an array of 2 elements
export const validateFilter = (filter) => {
  return (
    filter.columnId.length > 0 &&
    filter.filterConfig.filterValue !== "" &&
    filter.filterConfig.filterOp !== "" &&
    filter.filterConfig.filterType !== "" &&
    (filter.filterConfig.filterOp === "between" ||
    filter.filterConfig.filterOp === "not_between"
      ? Array.isArray(filter.filterConfig.filterValue) &&
        filter.filterConfig?.filterValue?.length === 2 &&
        filter.filterConfig.filterValue[0] != null &&
        filter.filterConfig.filterValue[0] !== "" &&
        filter.filterConfig.filterValue[1] != null &&
        filter.filterConfig.filterValue[1] !== ""
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

export const transformFilter = (filter) =>
  buildApiFilterFromPanelRow({
    field: filter.columnId,
    fieldType: filter.filterConfig.filterType,
    operator: filter.filterConfig.filterOp,
    value: transformFilterValue(
      filter.filterConfig.filterValue,
      filter.filterConfig.filterType,
    ),
  });

export const compareFilterChange = (prevFilters, filters) => {
  if (!prevFilters || !filters) return false;

  const validPrevFilters = prevFilters.filter(validateFilter);
  const validFilters = filters.filter(validateFilter);

  const prevHash = JSON.stringify(validPrevFilters.map(transformFilter));

  const currentHash = JSON.stringify(validFilters.map(transformFilter));

  return prevHash === currentHash;
};
