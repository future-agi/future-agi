import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const useRerunTest = (executionId, options) => {
  return useMutation({
    mutationFn: async (data) =>
      axios.post(endpoints.testExecutions.rerunExecution(executionId), data),
    ...options,
  });
};

export const useExecutionReproducibility = (executionId, options = {}) => {
  return useQuery({
    queryKey: ["test-execution-reproducibility", executionId],
    queryFn: () => axios.get(endpoints.testExecutions.reproducibility(executionId)),
    enabled: !!executionId,
    select: (data) => data.data,
    ...options,
  });
};

export const useGetTestDetail = (testId, options = {}) => {
  return useQuery({
    queryKey: ["test-runs-detail", testId],
    queryFn: () => axios.get(endpoints.runTests.detail(testId)),
    enabled: !!testId,
    select: (data) => data.data,
    ...options,
  });
};

export const useOptimizeTrialPrompts = ({ optimizationId, trialId }) => {
  return useQuery({
    queryKey: ["fix-my-agent-trail-prompts", optimizationId, trialId],
    queryFn: () =>
      axios.get(
        endpoints.optimizeSimulate.getTrailPrompts(optimizationId, trialId),
      ),
    select: (data) => data?.data?.result,
    enabled: !!optimizationId && !!trialId,
  });
};
