import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/**
 * Shared hook for canceling test/simulation executions
 * @param {{ errorHandled?: boolean }} [options] errorHandled: the caller shows
 *   its own error, so the app-wide error toast stays quiet
 * @returns {Object} mutation object with mutate function that accepts (id, options)
 */
export const useCancelExecution = ({ errorHandled = false } = {}) => {
  return useMutation({
    mutationFn: (id) =>
      axios.post(endpoints.testExecutions.cancelExecution(id),{}),
    ...(errorHandled && { meta: { errorHandled: true } }),
  });
};

export default useCancelExecution;
