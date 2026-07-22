import { useMemo } from "react";
import { useUrlState } from "src/routes/hooks/use-url-state";
import useReverseEvalFilters from "src/hooks/use-reverse-eval-filters";
import { convertToISO } from "../ChartsView/ChartsViewProvider/common";

// Helper to check if filters are in default state (no active filters)
const isDefaultFilters = (filters) => {
  if (!Array.isArray(filters)) return true;
  return filters.every((f) => !f?.column_id || f.column_id === "");
};

export const useLLMTracingFilters = (
  defaultFilter,
  defaultDateFilter,
  mainFilterName,
  dateFilterName,
  columns,
  getFilterExtraProperties,
) => {
  const [filters, setFilters] = useUrlState(mainFilterName, defaultFilter);

  const [dateFilter, setDateFilter] = useUrlState(
    dateFilterName,
    defaultDateFilter,
  );

  const reverseEvalColumnIds = useMemo(() => {
    return columns.filter((c) => c?.reverseOutput).map((c) => c.id);
  }, [columns]);

  // Check if filters are default to bypass expensive computation
  const hasDefaultFilters = useMemo(() => isDefaultFilters(filters), [filters]);

  const validatedMainFilters = useReverseEvalFilters(
    filters,
    reverseEvalColumnIds,
    getFilterExtraProperties,
  );

  const validatedFilters = useMemo(() => {
    // Build date filter once
    const dateFilterEntry = {
      column_id: "created_at",
      filter_config: {
        filter_type: "datetime",
        filter_op: "between",
        filter_value: convertToISO([
          new Date(dateFilter?.dateFilter[0]).toISOString(),
          new Date(dateFilter?.dateFilter[1]).toISOString(),
        ]),
      },
    };

    // Early return for default filters - skip waiting for debounced validatedMainFilters
    if (hasDefaultFilters) {
      return [dateFilterEntry];
    }

    return [...validatedMainFilters, dateFilterEntry];
  }, [dateFilter, validatedMainFilters, hasDefaultFilters]);

  return { filters, setFilters, validatedFilters, setDateFilter, dateFilter };
};
