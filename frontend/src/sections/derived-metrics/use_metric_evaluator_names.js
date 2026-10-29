import { useQueries } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/** Names are display metadata, never a replacement for the frozen ID/hash. */
export default function useMetricEvaluatorNames(
  projectId,
  inputs = [],
  scope = "",
  listed = [],
) {
  const ids = [
    ...new Set(
      inputs
        .filter((input) => input.source === "eval" && input.config_id)
        .map((input) => input.config_id),
    ),
  ];
  const queries = useQueries({
    queries: ids.map((id) => ({
      queryKey: ["metric-evaluator-name", scope, projectId, id],
      enabled: Boolean(
        projectId &&
          endpoints.project?.updateEvalTaskConfig &&
          !listed.some((item) => item.id === id && item.name),
      ),
      staleTime: 30_000,
      retry: false,
      queryFn: async ({ signal }) => {
        const { data } = await axios.get(
          endpoints.project.updateEvalTaskConfig(id),
          { signal },
        );
        if (data?.id !== id || data?.project !== projectId || !data?.name)
          throw new Error("Evaluator name is unavailable in this project.");
        return data.name;
      },
    })),
  });
  return Object.fromEntries(
    ids.map((id, index) => [
      id,
      listed.find((item) => item.id === id && item.name)?.name ||
        queries[index].data ||
        (queries[index].isFetching
          ? "Saved evaluator (loading name)"
          : "Saved evaluator (name unavailable; binding preserved)"),
    ]),
  );
}
