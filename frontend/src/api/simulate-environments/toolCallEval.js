import { useMutation, useQueryClient } from "@tanstack/react-query";
import { setToolCallEvaluation } from "src/api/simulate-environments/harnessEnvironments";
import { harnessEnvironmentKey } from "src/api/simulate-environments/environment";

// Persist the environment's tool-call evaluation switch. The response is the
// full environment detail, so seed the detail cache from it — the switch reads
// `settings.enable_tool_evaluation` from that detail. The caller renders the
// refusal (409 for a voice environment with no agent version), so the global
// toast is muted.
export function useToolCallEval() {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: ({ envId, enabled }) => setToolCallEvaluation(envId, enabled),
    onSuccess: (detail, { envId }) => {
      if (detail) queryClient.setQueryData(harnessEnvironmentKey(envId), detail);
    },
  });
}
