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
 * Runs a node's registered runner with test data (possibly-unsaved config
 * plus sample inputs) and returns its output. Never persists anything —
 * running a test has no effect on the node, the draft, or the saved
 * workflow, so it can be called for a node being edited before it's saved.
 *
 * @param {Object} payload
 * @param {string} payload.graphId - Graph ID
 * @param {string} payload.versionId - Version ID
 * @param {string} payload.nodeId - Node ID
 * @param {Object} [payload.promptTemplate] - Unsaved prompt form values
 *   (llm_prompt nodes only). Same shape as the node's saved prompt_template.
 *   Omit to test the node's last-saved config.
 * @param {Object} [payload.inputs] - Sample input port values (routing key -> value)
 * @returns {Promise<{status: "SUCCESS"|"FAILED", outputs: Object, error: string|null}>}
 */
export const testNodeApi = async (payload) => {
  const res = await axios.post(
    endpoints.agentPlayground.testNode(
      payload.graphId,
      payload.versionId,
      payload.nodeId,
    ),
    {
      prompt_template: payload.promptTemplate ?? null,
      inputs: payload.inputs ?? {},
    },
  );
  return res.data?.result;
};

/**
 * React Query mutation hook for test-running a node before saving it.
 * @param {object} options - useMutation options
 */
export const useTestNode = (options = {}) =>
  useMutation({
    mutationFn: testNodeApi,
    meta: { errorHandled: true },
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
