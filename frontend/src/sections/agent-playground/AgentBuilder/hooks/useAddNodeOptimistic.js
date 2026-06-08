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

const messageContentForApi = (content) =>
  Array.isArray(content) ? content : [{ type: "text", text: content || "" }];

const defaultPromptMessages = () => [
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
];

const promptMessagesForApi = (config) =>
  config?.messages?.length
    ? config.messages.map((message) => ({
        id: message.id || crypto.randomUUID(),
        role: message.role,
        content: messageContentForApi(message.content),
      }))
    : defaultPromptMessages();

const promptTemplateForApi = (config = {}) => {
  const modelConfig = config?.modelConfig || {};
  const promptConfiguration = config?.payload?.promptConfig?.[0]?.configuration;
  const baseTemplate = {
    prompt_template_id: config?.prompt_template_id ?? null,
    prompt_version_id: config?.prompt_version_id ?? null,
    messages: promptMessagesForApi(config),
  };

  if (!modelConfig.model) {
    return baseTemplate;
  }

  return {
    ...baseTemplate,
    model: modelConfig.model || null,
    model_detail: modelConfig.modelDetail || null,
    response_format:
      modelConfig.responseFormat ||
      promptConfiguration?.responseFormat ||
      promptConfiguration?.response_format ||
      "text",
    response_schema: modelConfig.responseSchema || null,
    output_format: config?.outputFormat || "string",
    template_format: config?.templateFormat || "mustache",
    tools: modelConfig.tools || promptConfiguration?.tools || [],
    tool_choice:
      modelConfig.toolChoice ||
      promptConfiguration?.toolChoice ||
      promptConfiguration?.tool_choice ||
      "auto",
    temperature: promptConfiguration?.temperature ?? null,
    max_tokens:
      promptConfiguration?.max_tokens ?? promptConfiguration?.maxTokens ?? null,
    top_p: promptConfiguration?.top_p ?? promptConfiguration?.topP ?? null,
    frequency_penalty:
      promptConfiguration?.frequency_penalty ??
      promptConfiguration?.frequencyPenalty ??
      null,
    presence_penalty:
      promptConfiguration?.presence_penalty ??
      promptConfiguration?.presencePenalty ??
      null,
  };
};

/**
 * Encapsulates the addNode pattern with optimistic-first approach:
 *
 * 1. addOptimisticNode adds the node to the store immediately (regardless of active/draft).
 * 2. ensureDraft is called — on active versions this POSTs the full snapshot (including
 *    the new node) and resolves "created"; the IDs are remapped in the store automatically.
 * 3. On draft versions, fires the individual addNodeApi call in the background sequentially.
 * 4. Rolls back on any failure.
 */
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

      const createNodeRequest = addNodeApi({
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
          ports,
          ...(payload.type === NODE_TYPES.LLM_PROMPT && {
            prompt_template: promptTemplateForApi(config),
          }),
        },
      });

      createNodeRequest
        .then((apiResult) => {
          if (edgeId && apiResult?.nodeConnection?.id) {
            useAgentPlaygroundStore
              .getState()
              .updateEdgeId(edgeId, apiResult.nodeConnection.id);
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

      if (payload.waitForApi) {
        try {
          await createNodeRequest;
        } catch {
          return null;
        }
      }

      return { nodeId, position };
    },
    [addOptimisticNode, removeOptimisticNode, setSelectedNode, ensureDraft],
  );

  return { addNode };
}
