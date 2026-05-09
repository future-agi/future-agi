import { getRandomId } from "src/utils/utils";
import {
  CODE_EXECUTION_DEFAULT_CONFIG,
  NODE_TYPES,
} from "../../utils/constants";

/**
 * Normalize responseFormat to always return a string (for dropdown/form use).
 * Extracts UUID from schema objects.
 * @param {string|Object} rf - responseFormat value (string or schema object)
 * @returns {string} "text", "json", "none", or a UUID string
 */
export function normalizeResponseFormat(rf) {
  if (rf && typeof rf === "object" && rf.id) return rf.id;
  if (!rf || rf === "string") return "text";
  return rf;
}

/**
 * Extract the full schema object from a responseFormat value.
 * @param {string|Object} rf - responseFormat value (string or schema object)
 * @returns {Object|null} Full schema object { id, name, schema } or null
 */
export function extractResponseSchema(rf) {
  if (rf && typeof rf === "object" && rf.id) return rf;
  return null;
}

export const KNOWN_FORMAT_VALUES = ["text", "json", "none", ""];

/**
 * Resolve responseFormat for API payloads.
 * Returns the full schema object when a custom schema is selected,
 * otherwise returns the string value. Only `response_format` is sent to the API.
 * @param {Object} modelConfig - { responseFormat: string, responseSchema: Object|null }
 * @returns {string|Object} Value to send as response_format in API payload
 */
export function resolveResponseFormatForApi(modelConfig) {
  const rf = modelConfig?.responseFormat || "text";
  if (!KNOWN_FORMAT_VALUES.includes(rf) && modelConfig?.responseSchema) {
    return modelConfig.responseSchema;
  }
  return rf;
}

/**
 * Build base form values from node metadata (not from node.data internals).
 */
function getBaseValues(nodeData) {
  return {
    nodeType: nodeData?.type || "",
    nodeId: nodeData?.id || "",
    name: nodeData?.data?.label || nodeData?.id || "",
  };
}

const PROMPT_DEFAULT_MODEL_CONFIG = {
  model: "",
  modelDetail: {
    modelName: "",
    logoUrl: "",
    providers: "",
    isAvailable: false,
  },
  toolChoice: "auto",
  tools: [],
  responseFormat: "text",
  responseSchema: null,
};

/**
 * Get default form values for a node by type.
 * Merges transient _initialConfig (from imports) over saved config.
 */
export function getDefaultValues(nodeData) {
  const baseValues = getBaseValues(nodeData);
  const savedConfig = nodeData?.data?.config;
  const initialConfig = nodeData?.data?._initialConfig;
  const mergedConfig = initialConfig
    ? { ...savedConfig, ...initialConfig }
    : savedConfig;
  const hasConfig = mergedConfig && Object.keys(mergedConfig).length > 0;

  if (nodeData?.type === NODE_TYPES.LLM_PROMPT) {
    if (hasConfig) {
      return {
        ...baseValues,
        version: mergedConfig.version || "",
        prompt_version_id:
          mergedConfig.prompt_version_id ||
          mergedConfig?.promptVersionId ||
          null,
        prompt_template_id:
          mergedConfig.prompt_template_id ||
          mergedConfig?.promptTemplateId ||
          null,
        outputFormat:
          mergedConfig.outputFormat ||
          mergedConfig.payload?.promptConfig?.[0]?.configuration
            ?.outputFormat ||
          "string",
        templateFormat:
          mergedConfig.templateFormat ||
          mergedConfig.payload?.promptConfig?.[0]?.configuration
            ?.template_format ||
          "mustache",
        modelConfig: mergedConfig.modelConfig || PROMPT_DEFAULT_MODEL_CONFIG,
        messages: mergedConfig.messages || [
          {
            id: getRandomId(),
            role: "system",
            content: [{ type: "text", text: "" }],
          },
          {
            id: getRandomId(),
            role: "user",
            content: [{ type: "text", text: "" }],
          },
        ],
      };
    }
    return {
      ...baseValues,
      version: "",
      prompt_version_id: null,
      prompt_template_id: null,
      outputFormat: "string",
      templateFormat: "mustache",
      modelConfig: PROMPT_DEFAULT_MODEL_CONFIG,
      messages: [
        {
          id: getRandomId(),
          role: "system",
          content: [{ type: "text", text: "" }],
        },
        {
          id: getRandomId(),
          role: "user",
          content: [{ type: "text", text: "" }],
        },
      ],
    };
  }

  if (nodeData?.type === NODE_TYPES.AGENT) {
    if (hasConfig) {
      return {
        ...baseValues,
        graphId: mergedConfig.graphId || "",
        versionId: mergedConfig.version_id || "",
        inputMappings: mergedConfig.payload?.inputMappings || [],
      };
    }
    return {
      ...baseValues,
      graphId: "",
      versionId: "",
      inputMappings: [],
    };
  }

  if (nodeData?.type === NODE_TYPES.CODE_EXECUTION) {
    return {
      ...baseValues,
      ...CODE_EXECUTION_DEFAULT_CONFIG,
      ...mergedConfig,
    };
  }

  if (nodeData?.type === "eval") {
    return {
      ...baseValues,
      evaluators: mergedConfig?.evaluators || [],
    };
  }

  return baseValues;
}

/**
 * Maps a GET /nodes/{id}/ response into the store node shape that getDefaultValues expects.
 * Reuses mapPatchResponseToStoreData for prompt nodes (same response shape as PATCH).
 *
 * @param {Object} apiNode - API node detail response
 * @param {Object} existingNode - Current node from store (preserves position, type, etc.)
 * @returns {Object} Merged node object compatible with getDefaultValues
 */
export function mapNodeDetailToNodeData(apiNode, existingNode) {
  if (!apiNode) return existingNode;

  const nodeType = existingNode?.type;

  if (nodeType === NODE_TYPES.LLM_PROMPT) {
    const pt = apiNode.promptTemplate;
    const storeData = {
      label: apiNode.name || existingNode?.data?.label,
      ports: (apiNode.ports || [])
        .filter((p) => (p.direction || p.direction) === "output")
        .map((p) => ({
          id: p.id,
          key: p.key,
          display_name: p.display_name,
          direction: p.direction,
          data_schema: p.dataSchema || p.data_schema,
          required: p.required,
        })),
    };

    if (pt) {
      storeData.config = {
        prompt_template_id: pt.promptTemplateId,
        prompt_version_id: pt.promptVersionId,
        outputFormat: pt.outputFormat || "string",
        templateFormat: pt.templateFormat || pt.template_format || "mustache",
        modelConfig: {
          model: pt.model || "",
          modelDetail:
            existingNode?.data?.config?.modelConfig?.modelDetail || {},
          responseFormat: normalizeResponseFormat(pt.response_format),
          responseSchema: extractResponseSchema(pt.response_format),
          toolChoice:
            pt.tool_choice ??
            existingNode?.data?.config?.model_config?.tool_choice ??
            "auto",
          tools:
            pt.tools ?? existingNode?.data?.config?.modelConfig?.tools ?? [],
        },
        messages: (() => {
          const mapped = (pt.messages || []).map((m, idx) => ({
            id: `msg-${idx}`,
            role: m.role,
            content:
              typeof m.content === "string"
                ? [{ type: "text", text: m.content }]
                : m.content || [{ type: "text", text: "" }],
          }));
          const hasSystem = mapped.some((m) => m.role === "system");
          if (!hasSystem) {
            mapped.unshift({
              id: getRandomId(),
              role: "system",
              content: [{ type: "text", text: "" }],
            });
          }
          const hasUser = mapped.some((m) => m.role === "user");
          if (!hasUser) {
            mapped.push({
              id: getRandomId(),
              role: "user",
              content: [{ type: "text", text: "" }],
            });
          }
          return mapped;
        })(),
        payload: {
          promptConfig: [
            {
              configuration: {
                temperature: pt.temperature,
                maxTokens: pt.max_tokens,
                topP: pt.top_p,
                frequencyPenalty: pt.frequencyPenalty,
                presencePenalty: pt.presencePenalty,
              },
            },
          ],
        },
      };
    }

    return {
      ...existingNode,
      data: {
        ...existingNode?.data,
        ...storeData,
        _initialConfig: undefined,
      },
    };
  }

  if (nodeType === NODE_TYPES.AGENT) {
    return {
      ...existingNode,
      data: {
        ...existingNode?.data,
        label: apiNode.name || existingNode?.data?.label,
        ports: apiNode.ports || existingNode?.data?.ports || [],
        versionId:
          apiNode.ref_graph_version_id || existingNode?.data?.version_id || "",
        graphId: apiNode.refGraphId || existingNode?.data?.graphId || "",
        ref_graph_version_id:
          apiNode.ref_graph_version_id ||
          existingNode?.data?.ref_graph_version_id ||
          "",
        config: {
          ...existingNode?.data?.config,
          graphId:
            apiNode.refGraphId || existingNode?.data?.config?.graphId || "",
          versionId:
            apiNode.ref_graph_version_id ||
            existingNode?.data?.config?.version_id ||
            "",
          payload: {
            ...existingNode?.data?.config?.payload,
            inputMappings: apiNode.inputMappings
              ? apiNode.inputMappings
              : existingNode?.data?.config?.payload?.inputMappings || [],
          },
        },
      },
    };
  }

  if (nodeType === NODE_TYPES.CODE_EXECUTION) {
    return {
      ...existingNode,
      data: {
        ...existingNode?.data,
        label: apiNode.name || existingNode?.data?.label,
        ports: apiNode.ports || existingNode?.data?.ports || [],
        config: apiNode.config || existingNode?.data?.config || {},
      },
    };
  }

  // Fallback: merge label and ports
  return {
    ...existingNode,
    data: {
      ...existingNode?.data,
      label: apiNode.name || existingNode?.data?.label,
      ports: apiNode.ports || existingNode?.data?.ports || [],
    },
  };
}
