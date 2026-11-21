import { useQuery } from "@tanstack/react-query";
import { readObserveProjectPage } from "src/api/project/observe-project-list";

/** Names for the given project ids, read from the existing Observe project
 * list (the same read the Metrics list uses). Pages until every id is found;
 * an id without a returned name stays absent, never guessed. */
export function useMetricProjectNames(scope, projectIds) {
  return useQuery({
    queryKey: ["metric-list-projects", scope, projectIds],
    enabled: projectIds.length > 0,
    staleTime: 30000,
    retry: false,
    placeholderData: undefined,
    meta: { errorHandled: true },
    queryFn: async ({ signal }) => {
      const names = {};
      const remaining = new Set(projectIds);
      for (let pageNumber = 0; remaining.size > 0; pageNumber += 1) {
        const page = await readObserveProjectPage({
          signal,
          params: { page_number: pageNumber, page_size: 50, name: "" },
        });
        signal.throwIfAborted();
        for (const project of page.rows) {
          if (remaining.has(project.id) && project.name) {
            names[project.id] = project.name;
            remaining.delete(project.id);
          }
        }
        if (pageNumber + 1 >= page.totalPages) break;
      }
      return names;
    },
  });
}

/** What to show for one project: its name, or an explicit loading or
 * unavailable state. */
export function metricProjectLabel(projectNames, projectId) {
  return (
    (!projectNames.isError && projectNames.data?.[projectId]) ||
    (projectNames.isPending ? "Loading project…" : "Project name unavailable")
  );
}
