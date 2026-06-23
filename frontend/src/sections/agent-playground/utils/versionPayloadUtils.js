import { NODE_TYPES, API_NODE_TYPES, VERSION_STATUS } from "./constants";
import {
  normalizeResponseFormat,
  extractResponseSchema,
  resolveResponseFormatForApi,
} from "../AgentBuilder/NodeDrawer/nodeFormUtils";

/**
 * Transform backend prompt_template (API schema) → form config (Zustand).
 * API shape:   { model: "gpt-4", messages: [{ role, content: "string" }], responseFormat: "string", ... }
 * Form shape:  { modelConfig: { model, ... }, messages: [{ id, role, content: [{type,text}] }] }
 */
function transformConfigFromApi(apiConfig) {
  if (!apiConfig || Object.keys(apiConfig).length === 0) return {};

  return {
    outputFormat: apiConfig.outputFormat || "string",
    modelConfig: {
      model: apiConfig.model || "",
      modelDetail: apiConfig.modelDetail || {
        modelName: apiConfig.model || "",
        logoUrl: "",
        providers: "",
        isAvailable: false,
      },
      responseFormat: normalizeResponseFormat(apiConfig.response_format),
      responseSchema: extractResponseSchema(apiConfig.response_format),
      toolChoice: apiConfig.tool_choice || "auto",
      tools: apiConfig.tools || [],
    },
    messages: (() => {
      const msgs = (apiConfig.messages || []).map((m, idx) => ({
        id: `msg-${idx}`,
        role: m.role,
        content:
          typeof m.content === "string"
            ? [{ type: "text", text: m.content }]
            : m.content || [{ type: "text", text: "" }],
      }));
      if (!msgs.some((m) => m.role === "system")) {
        msgs.unshift({
          id: "msg-sys",
          role: "system",
          content: [{ type: "text", text: "" }],
        });
      }
      return msgs;
    })(),
  };
}

/**
 * Build the POST payload for creating a new version.
 * Nodes carry their frontend-generated UUID as `id`,
 * and node_connections reference nodes via source_node_id / target_node_id.
 *
 * @param {Array} nodes - XYFlow nodes from Zustand store
 * @param {Array} edges - XYFlow edges from Zustand store
 * @param {Object} [options] - Optional overrides
 * @param {string} [options.status] - Version status ("draft" or "active")
 * @param {string} [options.commitMessage] - Commit message (for publishing)
 * @returns {{ status: string, nodes: Array, node_connections: Array }}
 */
export function buildVersionPayload(nodes = [], edges = [], options = {}) {
  const apiNodes = nodes.map((node) => {
    const isSubgraph = node.type === NODE_TYPES.AGENT;

    const apiNode = {
      id: node.id,
      type: isSubgraph ? API_NODE_TYPES.SUBGRAPH : API_NODE_TYPES.ATOMIC,
      name: node.data?.label || node.id,
      position: node.position,
    };

    if (isSubgraph) {
      apiNode.config = {};
      if (node.data?.ref_graph_version_id) {
        apiNode.ref_graph_version_id = node.data.ref_graph_version_id;
      }
      const mappings = node.data?.config?.payload?.inputMappings;
      if (Array.isArray(mappings) && mappings.length > 0) {
        apiNode.input_mappings = mappings;
      }
      const outputPorts = (node.data?.ports || []).filter(
        (p) => p.direction === "output",
      );
      if (outputPorts.length > 0) {
        apiNode.ports = outputPorts.map((port) => ({
          id: port.id || port.temp_id,
          key: port.key,
          display_name: port.display_name,
          direction: port.direction,
          data_schema: port.data_schema || port.dataSchema || {},
          required: port.required ?? true,
          ...(port.ref_port_id && { ref_port_id: port.ref_port_id }),
        }));
      }
    } else {
      apiNode.node_template_id = node.data?.node_template_id || null;
      // Merge transient _initialConfig (imported prompt data) over saved config
      // so the full prompt content is included in the API payload.
      const effectiveConfig = node.data?._initialConfig
        ? { ...node.data?.config, ...node.data._initialConfig }
        : node.data?.config;
      if (node.type === NODE_TYPES.CODE_EXECUTION) {
        apiNode.config = effectiveConfig || {};
      } else {
        apiNode.prompt_template = buildPromptTemplateForApi(effectiveConfig);
      }

      const atomicPorts = (node.data?.ports || []).filter(
        (port) =>
          node.type === NODE_TYPES.CODE_EXECUTION || port.direction === "output",
      );
      if (atomicPorts.length > 0) {
        apiNode.ports = atomicPorts.map((port) => ({
          id: port.id || port.temp_id,
          key: port.key,
          display_name: port.display_name,
          direction: port.direction,
          data_schema: port.data_schema || port.dataSchema || {},
          ...(port.ref_port_id && { ref_port_id: port.ref_port_id }),
        }));
      }
    }

    return apiNode;
  });

  // Node-to-node connections — reference nodes by id
  const apiNodeConnections = edges.map((e) => ({
    source_node_id: e.source,
    target_node_id: e.target,
  }));

  return {
    status: options.status || VERSION_STATUS.DRAFT,
    ...(options.commitMessage && { commit_message: options.commitMessage }),
    nodes: apiNodes,
    node_connections: apiNodeConnections,
  };
}

/**
 * Build prompt_template object for the API from store config shape.
 * @param {Object} formConfig - node.data.config from Zustand store
 * @returns {Object|null} PromptTemplateDataSerializer-shaped object
 */
function buildPromptTemplateForApi(formConfig) {
  if (!formConfig || Object.keys(formConfig).length === 0) return null;

  const model = formConfig.modelConfig?.model;
  if (!model) return null;

  const messages = (formConfig.messages || []).map((m) => ({
    id: m.id,
    role: m.role,
    content: Array.isArray(m.content)
      ? m.content
      : [{ type: "text", text: m.content || "" }],
  }));

  const configuration = formConfig.payload?.promptConfig?.[0]?.configuration;

  return {
    prompt_template_id: formConfig.prompt_template_id || null,
    prompt_version_id: formConfig.prompt_version_id || null,
    messages,
    model,
    model_detail: formConfig.modelConfig?.modelDetail || null,
    response_format: formConfig.modelConfig?.responseFormat
      ? resolveResponseFormatForApi(formConfig.modelConfig)
      : configuration?.responseFormat ||
        configuration?.response_format ||
        "text",
    output_format: "string",
    temperature: configuration?.temperature ?? null,
    max_tokens: configuration?.max_tokens ?? configuration?.max_tokens ?? null,
    top_p: configuration?.top_p ?? configuration?.top_p ?? null,
    frequency_penalty:
      configuration?.frequency_penalty ??
      configuration?.frequencyPenalty ??
      null,
    presence_penalty:
      configuration?.presence_penalty ?? configuration?.presencePenalty ?? null,
    tools: configuration?.tools || formConfig.modelConfig?.tools || [],
    tool_choice:
      configuration?.toolChoice ||
      configuration?.tool_choice ||
      formConfig.modelConfig?.toolChoice ||
      "",
    save_prompt_version: false,
  };
}

/**
 * Builds a payload for creating a new draft from an active version.
 * All existing node IDs are remapped to fresh UUIDs (each version owns its node IDs).
 *
 * @param {Array} nodes - XYFlow nodes from the current store (active version)
 * @param {Array} edges - XYFlow edges from the current store
 * @param {Function} [transformFn] - Optional: ({ nodes, edges, idMap }) => ({ nodes, edges })
 *   Receives nodes/edges with new IDs already applied + the old→new ID mapping.
 *   Return the final nodes/edges to include in the payload.
 * @returns {{ status: string, nodes: Array, node_connections: Array }}
 */
export function buildDraftCreationPayload(nodes = [], edges = [], transformFn) {
  // Generate a fresh UUID for every existing node
  const idMap = {};
  nodes.forEach((n) => {
    idMap[n.id] = crypto.randomUUID();
  });

  // Remap all existing nodes to new IDs
  const remappedNodes = nodes.map((n) => ({ ...n, id: idMap[n.id] }));

  // Remap all edges to use the new node IDs
  const remappedEdges = edges.map((e) => ({
    ...e,
    source: idMap[e.source] ?? e.source,
    target: idMap[e.target] ?? e.target,
  }));

  // Let the caller apply their specific mutation on top of remapped data
  const { nodes: finalNodes, edges: finalEdges } = transformFn
    ? transformFn({ nodes: remappedNodes, edges: remappedEdges, idMap })
    : { nodes: remappedNodes, edges: remappedEdges };

  return buildVersionPayload(finalNodes, finalEdges);
}

/**
 * Parse API version response into XYFlow nodes and edges.
 * Backend returns nodeConnections (camelCased from node_connections).
 *
 * @param {Object} apiData - API version detail response (result object)
 * @returns {{ nodes: Array, edges: Array }}
 */
export function parseVersionResponse(apiData) {
  if (!apiData?.nodes?.length) {
    return { nodes: [], edges: [] };
  }

  // Map API nodes → XYFlow nodes
  const xyNodes = apiData.nodes.map((node) => {
    const nodeType = mapApiTypeToNodeType(node);
    const isSubgraph = nodeType === NODE_TYPES.AGENT;
    const isCodeExecution = nodeType === NODE_TYPES.CODE_EXECUTION;
    const promptTemplate = node.promptTemplate ?? node.prompt_template;

    // For atomic nodes, read LLM config from promptTemplate (not config)
    const formConfig = isCodeExecution
      ? node.config || {}
      : isSubgraph || !promptTemplate
        ? null
        : transformConfigFromApi(promptTemplate);

    return {
      id: node.id,
      type: nodeType,
      position: node.position || { x: 0, y: 0 },
      data: {
        label: node.name || node.id,
        ...(!isSubgraph && {
          ports: (node.ports || []).map((p) => ({
            id: p.id,
            key: p.key,
            display_name: p.displayName || p.display_name,
            direction: p.direction,
            data_schema: p.dataSchema || p.data_schema || {},
            required: p.required,
          })),
        }),
        ...(isSubgraph
          ? {
              ports: (node.ports || []).map((p) => ({
                id: p.id,
                key: p.key,
                display_name: p.displayName || p.display_name,
                direction: p.direction,
                data_schema: p.dataSchema || p.data_schema || {},
                required: p.required,
                ...(p.refPortId && { ref_port_id: p.refPortId }),
              })),
              versionId:
                node.refGraphVersionId || node.ref_graph_version_id || "",
              graphId: node.refGraphId || node.ref_graph_id || "",
              config: {
                graphId: node.refGraphId || node.ref_graph_id || "",
                versionId:
                  node.refGraphVersionId || node.ref_graph_version_id || "",
                payload: {
                  inputMappings: node.inputMappings || node.input_mappings || [],
                },
              },
              ref_graph_version_id:
                node.refGraphVersionId || node.ref_graph_version_id || "",
            }
          : {
              node_template_id:
                node.nodeTemplateId || node.node_template_id || null,
              ...(isCodeExecution
                ? { config: node.config || {} }
                : {
                    prompt_template_id:
                      promptTemplate?.promptTemplateId ||
                      promptTemplate?.prompt_template_id ||
                      null,
                    prompt_version_id:
                      promptTemplate?.promptVersionId ||
                      promptTemplate?.prompt_version_id ||
                      null,
                    config: {
                      ...formConfig,
                      prompt_template_id:
                        promptTemplate?.promptTemplateId ||
                        promptTemplate?.prompt_template_id ||
                        null,
                      prompt_version_id:
                        promptTemplate?.promptVersionId ||
                        promptTemplate?.prompt_version_id ||
                        null,
                      payload: {
                        ...formConfig?.payload,
                        promptConfig: [
                          {
                            configuration: {
                              ...(promptTemplate || {}),
                              responseFormat:
                                promptTemplate?.responseFormat ||
                                promptTemplate?.response_format,
                            },
                          },
                        ],
                      },
                    },
                  }),
            }),
      },
    };
  });

  // Node connections → React Flow edges (id, source, target)
  const xyEdges = (apiData.nodeConnections || apiData.node_connections || [])
    .map((nc) => {
      const sourceNodeId = nc.sourceNodeId || nc.source_node_id;
      const targetNodeId = nc.targetNodeId || nc.target_node_id;
      if (!sourceNodeId || !targetNodeId) return null;
      return {
        id: nc.id || `${sourceNodeId}-${targetNodeId}`,
        source: sourceNodeId,
        target: targetNodeId,
      };
    })
    .filter(Boolean);

  return { nodes: xyNodes, edges: xyEdges };
}

/**
 * Map API node type to XYFlow node type.
 */
function mapApiTypeToNodeType(apiNode) {
  if (apiNode.type === API_NODE_TYPES.ATOMIC) {
    if (
      apiNode.nodeTemplateName === NODE_TYPES.CODE_EXECUTION ||
      apiNode.node_template_name === NODE_TYPES.CODE_EXECUTION
    ) {
      return NODE_TYPES.CODE_EXECUTION;
    }
    return NODE_TYPES.LLM_PROMPT;
  }
  if (apiNode.type === API_NODE_TYPES.SUBGRAPH) {
    return NODE_TYPES.AGENT;
  }
  return apiNode.type || NODE_TYPES.LLM_PROMPT;
}
