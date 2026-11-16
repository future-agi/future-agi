import { formatDate } from "src/utils/report-utils";
import { OBSERVE_LINK_FILTER_PARAM } from "src/sections/projects/LLMTracing/common";

import { monitorFiltersToTraceRows } from "./monitorFiltersToTraceRows";

// Deep-link from a fired alert issue to the traces behind it. The trace list
// hydrates both params on mount, so everything the user needs to see travels in
// the URL and the link stays shareable.
//
// The window is written in local time because that is what the date picker
// round-trips (see sections/projects/dateRangeDefaults.js); the log carries UTC
// ISO, so it goes through Date rather than string slicing, which would shift the
// range by the viewer's offset.
export const buildAlertTraceLink = ({
  projectId,
  windowStart,
  windowEnd,
  monitorFilters,
  metricType,
}) => {
  if (!projectId) return null;

  const params = new URLSearchParams({ tab: "traces", selectedTab: "trace" });

  if (windowStart && windowEnd) {
    params.set(
      "primaryTraceDateFilter",
      JSON.stringify({
        dateFilter: [
          formatDate(new Date(windowStart)),
          formatDate(new Date(windowEnd)),
        ],
        dateOption: "Custom",
      }),
    );
  }

  const filterRows = monitorFiltersToTraceRows(monitorFilters, { metricType });
  if (filterRows.length > 0) {
    params.set(OBSERVE_LINK_FILTER_PARAM, JSON.stringify(filterRows));
  }

  return `/dashboard/observe/${projectId}/llm-tracing?${params.toString()}`;
};
