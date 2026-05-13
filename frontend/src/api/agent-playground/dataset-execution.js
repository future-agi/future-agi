import {
  useMutation,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { EXECUTION_STATUS } from "src/sections/agent-playground/utils/workflowExecution";

/**
 * Hook for fetching the dataset linked to a graph.
 * @param {string} graphId
 * @param {string} versionId - used for cache separation per version
 * @param {object} options - Additional react-query options
 */
export const useGetGraphDataset = (graphId, versionId, options = {}) =>
  useQuery({
    queryKey: ["agent-playground", "graph-dataset", graphId, versionId],
    queryFn: ({ signal }) =>
      axios.get(endpoints.agentPlayground.graphDataset(graphId, versionId), {
        signal,
      }),
    select: (res) => res.data?.result,
    enabled: !!graphId,
    ...options,
  });

/**
 * Standalone async function for fetching dataset imperatively (bypasses React Query cache).
 * Used in useWorkflowExecution for fresh variable validation before run.
 * @param {string} graphId
 */
export const fetchGraphDataset = async (graphId, versionId) => {
  const res = await axios.get(
    endpoints.agentPlayground.graphDataset(graphId, versionId),
  );
  return res.data?.result;
};

/**
 * Hook for updating a single dataset cell value.
 * @param {object} options - useMutation options
 */
export const useUpdateDatasetCell = (options = {}) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ graphId, cellId, value }) =>
      axios.put(endpoints.agentPlayground.datasetCell(graphId, cellId), {
        value,
      }),
    onSuccess: (res, variables, context) => {
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graph-dataset", variables.graphId],
      });
      options.onSuccess?.(res, variables, context);
    },
    ...options,
  });
};

/**
 * Hook for executing the agent workflow for dataset rows.
 * POST /agent-playground/graphs/:graph_id/dataset/execute/
 * @param {object} options - useMutation options
 */
export const useExecuteDataset = (options = {}) =>
  useMutation({
    mutationFn: ({ graphId }) =>
      axios.post(endpoints.agentPlayground.executeDataset(graphId), {}),
    ...options,
  });

/**
 * Hook for evaluating a completed graph execution.
 * @param {object} options - useMutation options
 */
export const useEvaluateExecution = (options = {}) =>
  useMutation({
    mutationFn: ({ graphId, executionId, payload }) =>
      axios.post(
        endpoints.agentPlayground.evaluateExecution(graphId, executionId),
        payload,
      ),
    ...options,
  });

/**
 * Hook for fetching a single node's execution detail (inputs, outputs, usage).
 * @param {string} executionId
 * @param {string} nodeExecutionId
 * @param {object} options - Additional react-query options
 */
export const useGetNodeExecutionDetail = (
  executionId,
  nodeExecutionId,
  options = {},
) =>
  useQuery({
    queryKey: [
      "agent-playground",
      "node-execution-detail",
      executionId,
      nodeExecutionId,
    ],
    queryFn: ({ signal }) =>
      axios.get(
        endpoints.agentPlayground.nodeExecutionDetail(
          executionId,
          nodeExecutionId,
        ),
        { signal },
      ),
    select: (res) => res.data?.result,
    enabled: !!executionId && !!nodeExecutionId,
    staleTime: 30 * 1000,
    ...options,
  });

/**
 * Hook for polling execution detail until completion.
 * Polls every 2s while status is "running", stops on "success" or "error".
 * @param {string} graphId
 * @param {string} executionId
 * @param {object} options - Additional react-query options
 */
export const useGetExecutionDetail = (graphId, executionId, options = {}) =>
  useQuery({
    queryKey: ["agent-playground", "execution-detail", graphId, executionId],
    queryFn: ({ signal }) =>
      axios.get(
        endpoints.agentPlayground.executionDetail(graphId, executionId),
        { signal },
      ),
    select: (res) => res.data?.result,
    enabled: !!graphId && !!executionId,
    refetchInterval: ({ state }) => {
      const status = state?.data?.data?.result?.status?.toLowerCase?.();
      // Stop polling only on terminal statuses
      if (
        status === EXECUTION_STATUS.SUCCESS ||
        status === EXECUTION_STATUS.ERROR ||
        status === EXECUTION_STATUS.FAILED
      ) {
        return false;
      }
      return 2000;
    },
    staleTime: 0,
    ...options,
  });

/**
 * Hook for fetching graph executions with infinite scroll support.
 * Returns all executions ordered newest first. Pagination-ready via getNextPageParam.
 * @param {string} graphId
 * @param {object} options - Additional react-query options
 */
export const useGetExecutions = (graphId, options = {}) =>
  useInfiniteQuery({
    queryKey: ["agent-playground", "graph-executions", graphId],
    queryFn: ({ pageParam, signal }) =>
      axios.get(endpoints.agentPlayground.graphExecutions(graphId), {
        params: { page_number: pageParam, page_size: 20 },
        signal,
      }),
    getNextPageParam: (lastPage) =>
      lastPage.data?.result?.metadata?.next_page ?? undefined,
    initialPageParam: 1,
    enabled: !!graphId,
    ...options,
  });
