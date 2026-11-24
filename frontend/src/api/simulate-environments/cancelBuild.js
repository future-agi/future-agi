import { useMutation, useQueryClient } from "@tanstack/react-query";
import { cancelHarnessJob } from "src/api/harness/harness";
import { myEnvironmentsListKey } from "./environments";

/**
 * Cancel an in-progress environment build (§ POST harness-jobs/{id}/cancel/).
 *
 * The build page and the workspace both read the ["harness-job", id] poll as
 * their heartbeat, so on success we invalidate it (plus the environment detail
 * and the My-Environments list). The next poll sees the job's `canceled` stage,
 * which maps to buildStatus "failed" — the build view already renders that
 * failed state, so nothing else has to change here.
 *
 * `meta.errorHandled` lets the caller own the failure message (a cancel that
 * fails quietly reads as a dead button), so the global toast stays out of it.
 *
 * The job and detail keys are the literals environment.js defines
 * (["harness-job", id] and harnessEnvironmentKey === ["harness-environment", id]).
 */
export function useCancelHarnessJob(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (reason) => cancelHarnessJob(envId, reason),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ["harness-job", envId] }),
        queryClient.invalidateQueries({
          queryKey: ["harness-environment", envId],
        }),
        queryClient.invalidateQueries({ queryKey: myEnvironmentsListKey() }),
      ]),
  });
}
