import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/**
 * Creates a node via POST.
 *
 * @param {Object} payload
 * @param {string} payload.graphId - Graph ID
 * @param {string} payload.versionId - Version ID
 * @param {Object} payload.data - Contract-shaped POST body
 * @returns {Promise<Object>} Full node object from backend
 */
export const addNodeApi = async (payload) => {
  const res = await axios.post(
    endpoints.agentPlayground.addNode(payload.graphId, payload.versionId),
    payload.data,
  );
  return res.data?.result;
};

/**
 * React Query mutation hook for adding a node.
 * Uses dummy addNodeApi for now.
 * @param {object} options - useMutation options
 */
export const useAddNode = (options = {}) =>
  useMutation({
    mutationFn: addNodeApi,
    ...options,
  });

/**
 * Partially updates a node via PATCH.
 *
 * @param {Object} payload
 * @param {string} payload.graphId - Graph ID
 * @param {string} payload.versionId - Version ID
 * @param {string} payload.nodeId - Node ID
 * @param {Object} payload.data - Contract-shaped PATCH body
 * @returns {Promise<Object>} Full updated node from backend
 */
export const updateNodeApi = async (payload) => {
  const res = await axios.patch(
    endpoints.agentPlayground.updateNode(
      payload.graphId,
      payload.versionId,
      payload.nodeId,
    ),
    payload.data,
  );
  return res.data?.result;
};

/**
 * React Query mutation hook for partially updating a node.
 * @param {object} options - useMutation options
 */
export const useUpdateNode = (options = {}) =>
  useMutation({
    mutationFn: updateNodeApi,
    meta: { errorHandled: true },
    ...options,
  });

const canonicalCodeExecutionResult = (result) => {
  if (!result || typeof result !== "object") return result;

  const metadata = result.metadata || {};
  return {
    ok: result.ok,
    value: result.value,
    stdout: result.stdout,
    stderr: result.stderr,
    exit_code: result.exit_code ?? result.exitCode ?? null,
    duration_ms: result.duration_ms ?? result.durationMs ?? 0,
    error: result.error ?? null,
    metadata: {
      language: metadata.language,
      runner: metadata.runner ?? null,
      timed_out: metadata.timed_out ?? metadata.timedOut ?? false,
      memory_mb: metadata.memory_mb ?? metadata.memoryMb ?? null,
    },
  };
};

const canonicalTestExecutionOutput = (output) => {
  if (!output?.result) return output;
  return { ...output, result: canonicalCodeExecutionResult(output.result) };
};

export const testNodeExecutionApi = async (payload) => {
  const res = await axios.post(
    endpoints.agentPlayground.testNodeExecution(
      payload.graphId,
      payload.versionId,
      payload.nodeId,
    ),
    payload.data,
  );
  return canonicalTestExecutionOutput(res.data?.result);
};

export const useTestNodeExecution = (options = {}) =>
  useMutation({
    mutationFn: testNodeExecutionApi,
    meta: { errorHandled: true },
    ...options,
  });

/**
 * Deletes a node via DELETE. Backend cascades: soft-deletes edges,
 * connections, ports, PromptTemplateNode, and the node itself.
 *
 * @param {Object} payload
 * @param {string} payload.graphId - Graph ID
 * @param {string} payload.versionId - Version ID
 * @param {string} payload.nodeId - Node ID
 * @returns {Promise<Object>} Deletion result
 */
export const deleteNodeApi = async (payload) => {
  const res = await axios.delete(
    endpoints.agentPlayground.deleteNode(
      payload.graphId,
      payload.versionId,
      payload.nodeId,
    ),
  );
  return res.data?.result;
};

/**
 * Partially updates a port via PATCH (currently only display_name).
 *
 * @param {Object} payload
 * @param {string} payload.graphId - Graph ID
 * @param {string} payload.versionId - Version ID
 * @param {string} payload.portId - Port ID
 * @param {Object} payload.data - PATCH body (e.g. { display_name })
 * @returns {Promise<Object>} Updated port from backend
 */
export const updatePortApi = async (payload) => {
  const res = await axios.patch(
    endpoints.agentPlayground.updatePort(
      payload.graphId,
      payload.versionId,
      payload.portId,
    ),
    payload.data,
  );
  return res.data?.result;
};

/**
 * React Query mutation hook for partially updating a port.
 * @param {object} options - useMutation options
 */
export const useUpdatePort = (options = {}) =>
  useMutation({
    mutationFn: updatePortApi,
    ...options,
  });

/**
 * Hook for fetching a single node's detail (ports, prompt_template, config).
 * @param {string} graphId
 * @param {string} versionId
 * @param {string} nodeId
 * @param {object} options - Additional react-query options
 */
export const useGetNodeDetail = (graphId, versionId, nodeId, options = {}) =>
  useQuery({
    queryKey: ["agent-playground", "node-detail", graphId, versionId, nodeId],
    queryFn: () =>
      axios.get(
        endpoints.agentPlayground.getNodeDetail(graphId, versionId, nodeId),
      ),
    select: (res) => res.data?.result,
    enabled: !!graphId && !!versionId && !!nodeId,
    staleTime: 0,
    refetchOnWindowFocus: false,
    ...options,
  });

/**
 * Fetches possible edge mappings for a node (available variables from connected nodes).
 * @param {string} graphId
 * @param {string} versionId
 * @param {string} nodeId
 * @param {object} options - Additional react-query options
 */
export const useGetPossibleEdgeMappings = (
  graphId,
  versionId,
  nodeId,
  options = {},
) =>
  useQuery({
    queryKey: [
      "agent-playground",
      "possible-edge-mappings",
      graphId,
      versionId,
      nodeId,
    ],
    queryFn: ({ signal }) =>
      axios.get(
        endpoints.agentPlayground.possibleEdgeMappings(
          graphId,
          versionId,
          nodeId,
        ),
        { signal },
      ),
    select: (res) => res.data?.result,
    enabled: !!graphId && !!versionId && !!nodeId,
    ...options,
  });
