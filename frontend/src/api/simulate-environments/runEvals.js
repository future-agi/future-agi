import { useMutation, useQueryClient } from "@tanstack/react-query";
import { runEvaluationsAgain } from "./harnessEnvironments";

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
 * Grade chosen evals again on one finished run of an environment, without
 * rerunning its calls. `id` is the environment id.
 *
 * Refreshing the run's reads is what makes the header and table pick up
 * `evaluating` straight away; both then poll until grading finishes.
 */
export function useRunNewEvals() {
  const queryClient = useQueryClient();
  return useMutation({
    // The drawer shows refusals itself.
    meta: { errorHandled: true },
    mutationFn: ({ id, executionId, evalConfigIds }) =>
      runEvaluationsAgain(id, executionId, evalConfigIds),
    onSuccess: (_data, { executionId }) => {
      queryClient.invalidateQueries({ queryKey: runResultsKey(executionId) });
      queryClient.invalidateQueries({ queryKey: runAnalyticsKey(executionId) });
      // A call drawer opened from here on must not serve the old verdict.
      queryClient.invalidateQueries({ queryKey: callDetailKeyPrefix });
    },
    // After a timeout or a server error grading may have started anyway, and
    // the header does not poll a run it reads as completed. A 409 means the
    // run has moved on from what the page shows. Any other 4xx is a refusal
    // that leaves the run as it was.
    onError: (error, { executionId }) => {
      const status = error?.statusCode;
      if (status == null || status >= 500 || status === 409) {
        queryClient.invalidateQueries({ queryKey: runResultsKey(executionId) });
      }
    },
  });
}
