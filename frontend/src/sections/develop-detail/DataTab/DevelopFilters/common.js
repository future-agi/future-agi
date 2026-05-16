import { format, isValid } from "date-fns";
import logger from "src/utils/logger";

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

  return (
    filter.columnId.length > 0 &&
    filterValue !== "" &&
    filterOp !== "" &&
    filterType !== "" &&
    (filterType === "datetime"
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
    logger.debug({ filterValue });
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

export const transformFilter = (filter) => ({
  column_id: filter.columnId,
  filter_config: {
    filter_type: filter.filterConfig.filterType,
    filter_op: filter.filterConfig.filterOp,
    filter_value: transformFilterValue(
      filter.filterConfig.filterValue,
      filter.filterConfig.filterType,
      filter.filterConfig.filterOp,
    ),
  },
});

export const compareFilterChange = (prevFilters, filters) => {
  if (!prevFilters || !filters) return false;

  const validPrevFilters = prevFilters.filter(validateFilter);
  const validFilters = filters.filter(validateFilter);

  const prevHash = JSON.stringify(validPrevFilters.map(transformFilter));

  const currentHash = JSON.stringify(validFilters.map(transformFilter));

  return prevHash === currentHash;
};
