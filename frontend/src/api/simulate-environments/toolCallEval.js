import { useMutation, useQueryClient } from "@tanstack/react-query";
import { setToolCallEvaluation } from "src/api/simulate-environments/harnessEnvironments";
import { harnessEnvironmentKey } from "src/api/simulate-environments/environment";
import { useOwnEnvironmentId } from "src/api/simulate-environments/ownEnvironment";

// Persist the environment's tool-call evaluation switch. The response is the
// full environment detail, so seed the detail cache from it — the switch reads
// `settings.enable_tool_evaluation` from that detail. The caller renders the
// refusal (409 for a voice environment with no agent version), so the global
// toast is muted.
export function useToolCallEval() {
  const queryClient = useQueryClient();
  const ownEnvironmentId = useOwnEnvironmentId();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: async ({ envId, enabled }) =>
      setToolCallEvaluation(await ownEnvironmentId(envId), enabled),
    onSuccess: (detail) => {
      if (detail)
        queryClient.setQueryData(harnessEnvironmentKey(detail.id), detail);
    },
  });
}
