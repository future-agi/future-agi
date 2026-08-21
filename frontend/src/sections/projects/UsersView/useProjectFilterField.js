import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchAllObserveProjects } from "src/api/project/observe-project-list";

// Fetches the workspace's observe projects and returns a filter-field spec
// compatible with TraceFilterPanel (`filterFields` prop). The resulting field
// uses static `choices` so the filter panel skips its usual dashboard-API
// value lookup.
//
// Returns `null` while projects are loading, or when `enabled` is false, so
// the caller can conditionally include it.
export default function useProjectFilterField({ enabled = true } = {}) {
  const { data: projects = [] } = useQuery({
    queryKey: ["user-detail-project-filter-options"],
    enabled,
    queryFn: ({ signal }) => fetchAllObserveProjects({ signal }),
    staleTime: 5 * 60_000,
    retry: false,
  });

  return useMemo(() => {
    if (!enabled) return null;
    if (!projects?.length) return null;
    const choices = projects.map((p) => ({
      value: String(p.id),
      label: p.name || p.id,
    }));
    return {
      id: "project_id",
      name: "Project",
      category: "system",
      type: "string",
      choices,
    };
  }, [enabled, projects]);
}
