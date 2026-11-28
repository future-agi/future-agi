import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export function useResumeEvalTask({ onSuccess, ...callbacks }) {
  const queryClient = useQueryClient();
  return useMutation({
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
