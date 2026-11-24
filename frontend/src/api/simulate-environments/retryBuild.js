import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

const retryPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/build/retry/", {
    id: envId,
  });

export const retryBuild = async (envId) =>
  (await axios.post(retryPath(envId), {})).data;

export function useRetryBuild(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: () => retryBuild(envId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["harness-job", envId] });
      queryClient.invalidateQueries({
        queryKey: ["harness-environment", envId],
      });
      queryClient.invalidateQueries({ queryKey: ["harness-jobs"] });
    },
  });
}
