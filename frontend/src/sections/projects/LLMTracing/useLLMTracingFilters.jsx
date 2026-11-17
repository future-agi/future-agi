import { useEffect, useMemo, useState } from "react";
import { useUrlState } from "src/routes/hooks/use-url-state";
import useReverseEvalFilters from "src/hooks/use-reverse-eval-filters";
import { convertToISO } from "../ChartsView/ChartsViewProvider/common";
import {
  observePresetDateFilter,
  presetToRange,
  resolvePresetDateFilter,
} from "../timeWindowPresets";
import { OBSERVE_LIST_REFRESH_EVENT } from "../observeEvents";

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

  const [rawDateFilter, setDateFilter] = useUrlState(
    dateFilterName,
    defaultDateFilter,
  );

  // A preset in the URL (from a saved view, a shared link or a reload on a
  // later day) still carries the dates it resolved to back then. Query fresh
  // dates from the first render, and write them back so the URL shows them.
  // The default isn't in the URL, so re-resolving it must not add it there.
  // Time can change the window without changing the stored URL/default value.
  const [presetRevision, setPresetRevision] = useState(0);
  const dateFilter = useMemo(
    () => resolvePresetDateFilter(rawDateFilter),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rawDateFilter, presetRevision],
  );

  useEffect(() => {
    // Custom and unsupported options are fixed windows, not live presets.
    if (!observePresetDateFilter(dateFilter?.dateOption)) return undefined;

    let timeoutId;
    const refreshPreset = () => {
      // Do not reset the datasource (or rewrite the URL) on same-window polls,
      // including when the implicit default still holds its original bounds.
      if (resolvePresetDateFilter(dateFilter) !== dateFilter) {
        setPresetRevision((revision) => revision + 1);
      }
    };
    const scheduleBoundary = () => {
      const now = new Date();
      const nextDay = new Date(now);
      nextDay.setHours(24, 0, 0, 0);
      let nextBoundary = nextDay.getTime();
      const dayOnly =
        dateFilter.dateOption === "Today" ||
        dateFilter.dateOption === "Yesterday";
      if (!dayOnly) {
        const hourMs = 60 * 60 * 1000;
        const nextHour = (Math.floor(now.getTime() / hourMs) + 1) * hourMs;
        const nextLocalHour = new Date(now);
        nextLocalHour.setHours(now.getHours() + 1, 0, 0, 0);
        const [start] = presetToRange(dateFilter.dateOption, now);
        // Calendar subtraction can cross a fractional DST offset. Schedule
        // the start bucket as well as wall-clock boundaries, not just UTC now.
        const nextStartHour =
          now.getTime() + hourMs - (start.getTime() % hourMs);
        nextBoundary = Math.min(
          nextBoundary,
          nextHour,
          nextLocalHour.getTime(),
          nextStartHour,
        );
      }
      timeoutId = window.setTimeout(() => {
        refreshPreset();
        scheduleBoundary();
      }, nextBoundary - now.getTime());
    };

    window.addEventListener("observe-refresh", refreshPreset);
    window.addEventListener(OBSERVE_LIST_REFRESH_EVENT, refreshPreset);
    scheduleBoundary();
    return () => {
      window.clearTimeout(timeoutId);
      window.removeEventListener("observe-refresh", refreshPreset);
      window.removeEventListener(OBSERVE_LIST_REFRESH_EVENT, refreshPreset);
    };
  }, [dateFilter]);
  useEffect(() => {
    if (dateFilter !== rawDateFilter && rawDateFilter !== defaultDateFilter) {
      setDateFilter(dateFilter);
    }
  }, [dateFilter, rawDateFilter, defaultDateFilter, setDateFilter]);

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
