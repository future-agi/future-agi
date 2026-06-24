import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const useRerunTest = (executionId, options) => {
  return useMutation({
    mutationFn: async (data) =>
      axios.post(endpoints.testExecutions.rerunExecution(executionId), data),
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

export const useApplyTrialPrompt = (options = {}) => {
  return useMutation({
    mutationFn: async ({ optimizationId, trialId }) =>
      axios.post(
        endpoints.optimizeSimulate.applyTrial(optimizationId, trialId),
        {},
      ),
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
