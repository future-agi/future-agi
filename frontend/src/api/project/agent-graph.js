import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/**
 * Fetch the aggregate agent graph for a project.
 *
 * @param {string} projectId - Project UUID.
 * @param {Array} filters - Canonical API filter list.
 * @param {object} options - Additional options.
 * @param {boolean} options.enabled - Whether the query should run.
 * @returns {import("@tanstack/react-query").UseQueryResult}
 */
export const useAgentGraph = (
  projectId,
  filters = [],
  { enabled = true } = {},
) => {
  return useQuery({
    queryKey: ["agent-graph", projectId, filters],
    queryFn: () =>
      axios.get(endpoints.project.getAgentGraph(), {
        params: {
          project_id: projectId,
          filters: JSON.stringify(filters || []),
        },
      }),
    select: (data) => data.data?.result,
    enabled: !!projectId && enabled,
    staleTime: 30_000,
    retry: false,
  });
};
