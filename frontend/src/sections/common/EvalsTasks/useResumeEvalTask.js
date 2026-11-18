import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";

export const RESUME_FAILED_FALLBACK =
  "Failed to resume task. It may have already finished.";

// The server says why it refused (e.g. a start that failed after the automatic
// retry). Show that; keep the generic text only when it sends no message.
export const resumeErrorMessage = (error) =>
  getSafeActionErrorMessage(error, RESUME_FAILED_FALLBACK);

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
