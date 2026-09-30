import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

// Prefixes of the run page's own reads: the header summary and every table
// page share the first, analytics has its own.
export const runResultsKey = (executionId) => [
  "simulation-run-results-v3",
  executionId,
];
export const runAnalyticsKey = (executionId) => [
  "simulation-run-analytics-v3",
  executionId,
];
// Every call drawer's detail. Each is keyed by its call alone, and a
// re-grade touches every call of the run, so the whole prefix is refreshed.
export const callDetailKeyPrefix = ["simulation-call-detail-v3"];

/**
 * Grade chosen evals again on one finished run, without rerunning its calls.
 *
 * Refreshing the run's reads is what makes the header and table pick up
 * `evaluating` straight away; both then poll until grading finishes.
 */
export function useRunNewEvals() {
  const queryClient = useQueryClient();
  return useMutation({
    // The drawer shows refusals itself.
    meta: { errorHandled: true },
    mutationFn: ({ runTestId, executionId, evalConfigIds }) =>
      axios
        .post(endpoints.runTests.runEvals(runTestId), {
          test_execution_ids: [executionId],
          eval_config_ids: evalConfigIds,
        })
        .then((response) => response.data),
    onSuccess: (_data, { executionId }) => {
      queryClient.invalidateQueries({ queryKey: runResultsKey(executionId) });
      queryClient.invalidateQueries({ queryKey: runAnalyticsKey(executionId) });
      // A call drawer opened from here on must not serve the old verdict.
      queryClient.invalidateQueries({ queryKey: callDetailKeyPrefix });
    },
  });
}
