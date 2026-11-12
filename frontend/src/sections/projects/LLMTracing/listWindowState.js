import { useQuery } from "@tanstack/react-query";
import { addHours, formatDistanceToNow, isSameDay } from "date-fns";
import { readObserveProjectPage } from "src/api/project/observe-project-list";
import { buildProjectListApiFilters } from "src/sections/project/common";
import { fDate, fDateTime, toValidDate } from "src/utils/format-time";
import { observePresetDateFilter } from "../timeWindowPresets";

// The date window, last-trace read and wording behind the Trace/Spans
// out-of-window empty state (ListWindowEmptyState).

// "Show all time" picks the widest window the date picker offers: Past 12M
// (DateRangePill DATE_OPTIONS, timeWindowPresets OBSERVE_PRESETS). There is no
// all-time option, and an open-ended range would be an unbounded list read.
// The last trace this state names is at most 90 days old (see below), so the
// 12-month window always holds it.
export const LIST_ALL_TIME_OPTION = "12M";

export const showAllTimeDateFilter = (prev, now = new Date()) => ({
  ...prev,
  dateFilter: observePresetDateFilter(LIST_ALL_TIME_OPTION, now),
  dateOption: LIST_ALL_TIME_OPTION,
});

/**
 * The half-open [start, end) window the list read, parsed as the request is
 * built (useLLMTracingFilters), and the last instant it covers: a window that
 * ends at a local midnight reads as ending the day before.
 */
export const getListWindowBounds = (dateFilter) => {
  const range = dateFilter?.dateFilter;
  if (!Array.isArray(range) || range.length < 2) return null;
  const start = new Date(range[0]);
  const end = new Date(range[1]);
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime())) {
    return null;
  }
  if (end <= start) return null;
  const endsAtMidnight =
    end.getHours() === 0 && end.getMinutes() === 0 && end.getSeconds() === 0;
  return {
    start,
    end,
    lastCovered: endsAtMidnight ? new Date(end.getTime() - 1000) : end,
  };
};

/**
 * What the empty state of a primary list needs: the project to read and the
 * list's own date window and setter. Any filter besides the date (filter-panel
 * rows, errors / non-annotated toggles, the eval filter) makes an empty page a
 * "no match", so those lists get none. Chip filters are checked by the grid.
 */
export const buildListWindow = ({
  projectId,
  projectName,
  dateFilter,
  setDateFilter,
  validatedFilters = [],
  metricFilters = [],
  hasEvalFilter = false,
}) => {
  if (!projectId || !projectName || typeof setDateFilter !== "function") {
    return null;
  }
  if (!getListWindowBounds(dateFilter)) return null;
  if (hasEvalFilter || metricFilters?.length > 0) return null;
  const otherFilter = (validatedFilters || []).some(
    (filter) => filter?.column_id && filter.column_id !== "created_at",
  );
  if (otherFilter) return null;
  return { projectId, projectName, dateFilter, setDateFilter };
};

/**
 * list_projects reads last_active from the hourly trace_count_rollup over the
 * last 90 days: the start of the hour that holds the project's newest trace
 * (futureagi/tracer/views/project.py list_projects). null when the project
 * has no trace in that span or its activity could not be read.
 */
export const getProjectLastTrace = (row) => {
  if (!row || row.activity_query_complete === false) return null;
  const hourStart = toValidDate(row.last_active);
  if (!hourStart) return null;
  return { hourStart, hourEnd: addHours(hourStart, 1) };
};

// Only an hour wholly before or after the window proves the window is empty
// for a reason other than "nothing traced yet".
export const isOutsideListWindow = (lastTrace, bounds) =>
  Boolean(lastTrace && bounds) &&
  (lastTrace.hourEnd <= bounds.start || lastTrace.hourStart >= bounds.end);

/**
 * "between <start> and <end>": dates, or date and time when the window, or
 * the last trace and a window edge, share a day.
 */
export const describeListWindow = (bounds, lastTrace) => {
  const { start, lastCovered } = bounds;
  const oneDay = isSameDay(start, lastCovered);
  const withTime =
    oneDay ||
    (lastTrace &&
      (isSameDay(lastTrace.hourStart, start) ||
        isSameDay(lastTrace.hourStart, lastCovered)));
  if (!withTime) return `between ${fDate(start)} and ${fDate(lastCovered)}`;
  const endText = oneDay ? fDateTime(lastCovered, "p") : fDateTime(lastCovered);
  return `between ${fDateTime(start)} and ${endText}`;
};

// Relative time from the end of the hour, as the project list's Last Active
// column measures it, so one project reads the same in both places.
export const describeLastTrace = (
  { hourStart, hourEnd },
  now = new Date(),
) => ({
  relative:
    hourEnd > now
      ? "in the last hour"
      : formatDistanceToNow(hourEnd, { addSuffix: true }),
  date: fDate(hourStart),
});

// list_projects caps a page at 100 rows; an exact-name match rarely holds
// more than one project, and the row is picked by id.
const LAST_TRACE_PAGE_SIZE = 20;
const LAST_TRACE_STALE_MS = 60_000;

/**
 * One bounded read, made only while an unfiltered list shows its empty state:
 * the project's own list_projects row (exact name, then id), the row the
 * project list's Last Active column shows. Both primary grids share it.
 */
export const useProjectLastTrace = (listWindow) => {
  const projectId = listWindow?.projectId;
  const projectName = listWindow?.projectName;
  return useQuery({
    queryKey: ["observe-project-last-trace", projectId, projectName],
    queryFn: ({ signal }) =>
      readObserveProjectPage({
        signal,
        params: {
          page_number: 0,
          page_size: LAST_TRACE_PAGE_SIZE,
          filters: buildProjectListApiFilters([
            { field: "name", operator: "in", value: projectName },
          ]),
        },
      }),
    enabled: Boolean(projectId && projectName),
    select: (page) =>
      getProjectLastTrace(
        page?.rows?.find((row) => String(row?.id) === String(projectId)),
      ),
    staleTime: LAST_TRACE_STALE_MS,
    retry: false,
  });
};
