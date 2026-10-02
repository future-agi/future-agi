import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/**
 * Shared hook for canceling test/simulation executions
 * @returns {Object} mutation object with mutate function that accepts (id, options)
 */
export const useCancelExecution = () => {
  return useMutation({
    mutationFn: (id) =>
      axios.post(endpoints.testExecutions.cancelExecution(id),{}),
  });
};

export default useCancelExecution;
