import { useMemo, useRef } from "react";
import { useGetValidatedFilters } from "./use-get-validated-filters";

const reverseFilter = (filterConfig) => {
  const fil = { ...filterConfig };
  switch (filterConfig.filter_op) {
    case "equals":
    case "not_equals":
      fil.filter_value = 100 - filterConfig.filter_value;
      break;
    case "greater_than":
      fil.filter_op = "less_than";
      fil.filter_value = 100 - filterConfig.filter_value;
      break;
    case "less_than":
      fil.filter_op = "greater_than";
      fil.filter_value = 100 - filterConfig.filter_value;
      break;
    case "greater_than_or_equal":
      fil.filter_op = "less_than_or_equal";
      fil.filter_value = 100 - filterConfig.filter_value;
      break;
    case "less_than_or_equal":
      fil.filter_op = "greater_than_or_equal";
      fil.filter_value = 100 - filterConfig.filter_value;
      break;
    case "between":
    case "not_in_between":
      fil.filter_value = [
        100 - filterConfig.filter_value[1],
        100 - filterConfig.filter_value[0],
      ];
      break;
    default:
      break;
  }
  return fil;
};

const useReverseEvalFilters = (
  filters,
  reverseEvalColumnIds,
  getCustomProperties,
) => {
  const previousReverseFilters = useRef([]);
  const validatedFilters = useGetValidatedFilters(filters, getCustomProperties);

  const reverseEvalFilters = useMemo(() => {
    return validatedFilters.map((filter) => {
      if (reverseEvalColumnIds.includes(filter.column_id)) {
        return {
          ...filter,
          filter_config: {
            ...reverseFilter(filter.filter_config),
          },
        };
      }
      return filter;
    });
  }, [validatedFilters, reverseEvalColumnIds]);

  if (
    JSON.stringify(previousReverseFilters.current) ===
    JSON.stringify(reverseEvalFilters)
  ) {
    return previousReverseFilters.current;
  }
  previousReverseFilters.current = reverseEvalFilters;

  return reverseEvalFilters;
};

export default useReverseEvalFilters;
