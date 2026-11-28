import {
  useMutation,
  useQueryClient,
  type UseMutationOptions,
} from "@tanstack/react-query";
import type { AxiosResponse } from "axios";
import axios, { endpoints } from "src/utils/axios";

type ResumeCallbacks<TContext> = Pick<
  UseMutationOptions<AxiosResponse<unknown>, Error, string, TContext>,
  "onMutate" | "onError" | "onSuccess" | "onSettled"
>;

export function useResumeEvalTask<TContext>({
  onSuccess,
  ...callbacks
}: ResumeCallbacks<TContext>) {
  const queryClient = useQueryClient();
  return useMutation<AxiosResponse<unknown>, Error, string, TContext>({
    ...callbacks,
    mutationFn: (taskId) =>
      axios.post(endpoints.project.resumeEvalTask(taskId), {}),
    meta: { errorHandled: true },
    onSuccess: (response, taskId, context) => {
      // Terminal Logs no longer poll. Discard the old run's summary and, if
      // Logs is open, fetch the replacement immediately. Keep it on rejection.
      queryClient.resetQueries({ queryKey: ["eval-task-logs", taskId] });
      return onSuccess?.(response, taskId, context);
    },
  });
}
