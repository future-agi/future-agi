import { useCallback } from "react";
import { useUpdateNode } from "src/api/agent-playground/agent-playground";
import { useAgentPlaygroundStore } from "../../store";
import { NODE_TYPES, PORT_DIRECTION } from "../../utils/constants";
import { buildPatchPayload } from "../NodeDrawer/forms/promptNodeFormUtils";

/**
 * Build PATCH payload for agent (subgraph) nodes.
 * Mirrors the shape used by buildVersionPayload in versionPayloadUtils.
 */
function buildAgentPatchPayload(updateData) {
  const patch = {
    ref_graph_version_id: updateData.ref_graph_version_id,
    config: {},
  };

  if (updateData.label) patch.name = updateData.label;

  const ports = updateData.config?.payload?.ports;
  const outputPorts = ports?.filter(
    (p) => p.direction === PORT_DIRECTION.OUTPUT,
  );
  if (outputPorts?.length > 0) patch.ports = outputPorts;

  const inputMappings = updateData.config?.payload?.inputMappings;
  if (inputMappings && inputMappings.length > 0) {
    patch.input_mappings = inputMappings;
  }

  return patch;
}

/**
 * Wraps the partial update API.
 * Transforms store-shaped nodeUpdate into contract-shaped PATCH payload
 * using prompt_template_id/prompt_version_id from the store.
 */
export default function usePartialNodeUpdate() {
  const { mutateAsync, isPending } = useUpdateNode();

  const partialUpdate = useCallback(
    async (nodeId, updateData) => {
      const state = useAgentPlaygroundStore.getState();
      const node = state.getNodeById(nodeId);
      const config = node?.data?.config;
      const graphId = state.currentAgent?.id;
      const versionId = state.currentAgent?.version_id;
      const apiPayload =
        node?.type === NODE_TYPES.LLM_PROMPT
          ? buildPatchPayload(updateData, config)
          : node?.type === NODE_TYPES.CODE_EXECUTION
            ? {
                ...(updateData.label && { name: updateData.label }),
                config: updateData.config || {},
              }
            : buildAgentPatchPayload(updateData);

      return mutateAsync({ graphId, versionId, nodeId, data: apiPayload });
    },
    [mutateAsync],
  );

  return { partialUpdate, isPending };
}
