import { useQuery } from "@tanstack/react-query";
import { fetchAllRunCalls } from "../../../compare/useComparison";

const NO_TASKS = [];

export const runWidgetCallsKey = (executionId) => [
  "simulation-run-results-v3",
  executionId,
  "widget-calls",
];

/**
 * Every call of the run (up to the compare page's cap), mapped the way the
 * run's trace table maps them. The widget editor and each saved widget on the
 * Analytics tab share this one read.
 * @param {string} executionId
 */
export function useRunWidgetCalls(executionId) {
  const query = useQuery({
    queryKey: runWidgetCallsKey(executionId),
    queryFn: () => fetchAllRunCalls(executionId),
    enabled: !!executionId,
    staleTime: 1000 * 60,
  });
  return {
    tasks: query.data?.tasks ?? NO_TASKS,
    count: query.data?.count ?? 0,
    capped: !!query.data?.capped,
    isLoading: !!executionId && query.isPending,
    isError: query.isError,
    refetch: query.refetch,
  };
}
