import { useEffect, useRef } from "react";
import { useDashboardFilterValues } from "src/hooks/useDashboards";
import { useDebounce } from "src/hooks/use-debounce";

const TAG_SEARCH_DEBOUNCE_MS = 250;

const toTagNames = (values) => {
  const names = [];
  for (const option of values || []) {
    const name = typeof option === "string" ? option : option?.value;
    if (typeof name === "string" && name.trim() && !names.includes(name)) {
      names.push(name);
    }
  }
  return names;
};

/**
 * Existing tag names in a project that match `search`, for the tag input's
 * suggestions. Reads the same vocabulary as the trace filter's Tag field
 * (/tracer/dashboard/filter_values/ with metric_name=tag,
 * metric_type=system_metric, source=traces); the backend matches the search
 * anywhere in the name, ignoring case. Nothing is read without a project id or
 * typed text.
 */
export function useProjectTagNames({ projectId, search = "" }) {
  const typed = search.trim();
  const settled = useDebounce(typed, TAG_SEARCH_DEBOUNCE_MS);
  const enabled = Boolean(projectId) && settled.length > 0;
  const { data, isLoading, isError } = useDashboardFilterValues({
    metricName: "tag",
    metricType: "system_metric",
    source: "traces",
    projectIds: projectId ? [projectId] : [],
    search: settled,
    searchGesture: typed,
    enabled,
  });

  const loading = enabled && Boolean(isLoading);
  const fresh = enabled && !loading && !isError ? toTagNames(data) : null;

  // Each settled search is a new read. Keep the last names for this project
  // on screen while the next one loads, so the list doesn't blink between
  // keystrokes; the input still filters them by what is typed now.
  const lastRead = useRef({ projectId: null, names: [] });
  useEffect(() => {
    if (fresh) lastRead.current = { projectId, names: fresh };
  });

  if (!projectId || !typed) return { names: [], isLoading: false };
  if (fresh) return { names: fresh, isLoading: false };
  const kept =
    lastRead.current.projectId === projectId ? lastRead.current.names : [];
  return { names: kept, isLoading: loading };
}
