import { useCallback } from "react";
import { enqueueSnackbar } from "notistack";
import {
  useAgentPlaygroundStoreShallow,
  useAgentPlaygroundStore,
} from "../../store";
import { addNodeApi } from "src/api/agent-playground/agent-playground";
import logger from "src/utils/logger";
import { API_NODE_TYPES, NODE_TYPES } from "../../utils/constants";
import { useSaveDraftContext } from "../saveDraftContext";
import { buildPatchPayload } from "../NodeDrawer/forms/promptNodeFormUtils";
import { normalizePromptOutputPorts } from "../../utils/promptPortUtils";

/**
 * Encapsulates the addNode pattern with optimistic-first approach:
 *
 * 1. addOptimisticNode adds the node to the store immediately (regardless of active/draft).
 * 2. ensureDraft is called — on active versions this POSTs the full snapshot (including
 *    the new node) and resolves "created"; the IDs are remapped in the store automatically.
 * 3. On draft versions, fires the individual addNodeApi call in the background sequentially.
 * 4. Rolls back on any failure.
 */
function buildPromptTemplatePayload(config) {
  if (config?.messages?.length) {
    return buildPatchPayload({ config }, config).prompt_template;
  }

  return {
    prompt_template_id: config?.prompt_template_id ?? null,
    prompt_version_id: config?.prompt_version_id ?? null,
    messages: [
      {
        id: crypto.randomUUID(),
        role: "system",
        content: [{ type: "text", text: "" }],
      },
      {
        id: crypto.randomUUID(),
        role: "user",
        content: [{ type: "text", text: "" }],
      },
    ],
  };
}

export default function useAddNodeOptimistic() {
  const { addOptimisticNode, removeOptimisticNode, setSelectedNode } =
    useAgentPlaygroundStoreShallow((state) => ({
      addOptimisticNode: state.addOptimisticNode,
      removeOptimisticNode: state.removeOptimisticNode,
      setSelectedNode: state.setSelectedNode,
    }));
  const { ensureDraft } = useSaveDraftContext();

  const addNode = useCallback(
    async (payload) => {
      // Always apply optimistic edit first (regardless of active/draft)
      const result = addOptimisticNode(
        payload.type,
        payload.position,
        payload.sourceNodeId,
        payload.node_template_id,
        payload.name,
        payload.config,
      );

      if (!result) return null;

      const { nodeId, edgeId, position, ports, label } = result;
      const config = payload.config;
      const apiPorts =
        payload.type === NODE_TYPES.LLM_PROMPT
          ? normalizePromptOutputPorts(ports, config)
          : ports;

      // Don't select the node yet — wait until ensureDraft completes
      // to avoid triggering the discard dialog if a drawer is open with dirty form.
      const draftResult = await ensureDraft();

      if (draftResult === false) {
        // POST failed — rollback our optimistic edit
        removeOptimisticNode(nodeId);
        return null;
      }

      if (draftResult === "created") {
        // Node was included in the POST, IDs remapped. Select the remapped node.
        const remappedNode = useAgentPlaygroundStore
          .getState()
          .nodes.find((n) => n.data?.label === label);
        if (remappedNode) setSelectedNode(remappedNode);
        return { nodeId: remappedNode?.id || nodeId, position };
      }

      // Already a draft — fire individual API call.
      // Defer drawer opening until API confirms to prevent 404s on rapid clicks.
      const { currentAgent } = useAgentPlaygroundStore.getState();

      addNodeApi({
        graphId: currentAgent?.id,
        versionId: currentAgent?.version_id,
        data: {
          id: nodeId,
          type:
            payload.type === NODE_TYPES.AGENT
              ? API_NODE_TYPES.SUBGRAPH
              : API_NODE_TYPES.ATOMIC,
          name: label || payload.name || nodeId,
          node_template_id: payload.node_template_id,
          position,
          source_node_id: payload.sourceNodeId,
          ...(payload.sourceNodeId && edgeId && { edge_id: edgeId }),
          ports: apiPorts,
          ...(payload.type === NODE_TYPES.LLM_PROMPT && {
            prompt_template: buildPromptTemplatePayload(config),
          }),
        },
      })
        .then((apiResult) => {
          const nodeConnection =
            apiResult?.nodeConnection || apiResult?.node_connection;
          if (edgeId && nodeConnection?.id) {
            useAgentPlaygroundStore
              .getState()
              .updateEdgeId(edgeId, nodeConnection.id);
          }
          // Open drawer only after node exists on backend
          const addedNode = useAgentPlaygroundStore
            .getState()
            .getNodeById(nodeId);
          if (addedNode) setSelectedNode(addedNode);
        })
        .catch((error) => {
          logger.error("[useAddNodeOptimistic] addNodeApi failed", error);
          removeOptimisticNode(nodeId);
          enqueueSnackbar("Failed to add node", { variant: "error" });
        });

      return { nodeId, position };
    },
    [addOptimisticNode, removeOptimisticNode, setSelectedNode, ensureDraft],
  );

  return { addNode };
}
