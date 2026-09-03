import { useMemo } from "react";
import useReverseEvalFilters from "src/hooks/use-reverse-eval-filters";

export const useUserTraceFilter = (
  filters, // <- no longer internal state
  dateFilter,
  columns,
  getFilterExtraProperties,
) => {
  const reverseEvalColumnIds = useMemo(() => {
    return columns.filter((c) => c?.reverseOutput).map((c) => c.id);
  }, [columns]);

  const validatedMainFilters = useReverseEvalFilters(
    filters,
    reverseEvalColumnIds,
    getFilterExtraProperties,
  );

  const validatedFilters = useMemo(() => {
    const [start, end] = Array.isArray(dateFilter)
      ? dateFilter
      : dateFilter?.dateFilter || [];

    const isValidStart = start && !isNaN(new Date(start).getTime());
    const isValidEnd = end && !isNaN(new Date(end).getTime());

    const dateFilterClause =
      isValidStart && isValidEnd
        ? [
            {
              column_id: "created_at",
              filter_config: {
                filter_type: "datetime",
                filter_op: "between",
                filter_value: [
                  new Date(start).toISOString(),
                  new Date(end).toISOString(),
                ],
              },
            },
          ]
        : [];

    return [...validatedMainFilters, ...dateFilterClause];
  }, [dateFilter, validatedMainFilters]);

  return {
    validatedFilters,
  };
};
