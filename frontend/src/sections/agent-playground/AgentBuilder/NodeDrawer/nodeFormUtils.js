import { getRandomId } from "src/utils/utils";
import { NODE_TYPES } from "../../utils/constants";

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

export function resolveResponseSchema(responseFormat, responseSchema) {
  return responseSchema ?? extractResponseSchema(responseFormat);
}

export const KNOWN_FORMAT_VALUES = [
  "text",
  "json",
  "json_schema",
  "json_object",
  "string",
  "none",
  "",
];

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

function firstDefined(...values) {
  return values.find((value) => value !== undefined && value !== null);
}

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
          mergedConfig.payload?.promptConfig?.[0]?.configuration
            ?.output_format ||
          "string",
        templateFormat:
          mergedConfig.templateFormat ||
          mergedConfig.payload?.promptConfig?.[0]?.configuration
            ?.template_format ||
          "mustache",
        payload: mergedConfig.payload,
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
    const pt = apiNode.promptTemplate || apiNode.prompt_template;
    const storeData = {
      label: apiNode.name || existingNode?.data?.label,
      ports: (apiNode.ports || [])
        .filter((p) => p.direction === "output")
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
      const responseFormat = firstDefined(
        pt.responseFormat,
        pt.response_format,
      );
      const existingConfig = existingNode?.data?.config;
      storeData.config = {
        prompt_template_id: firstDefined(
          pt.promptTemplateId,
          pt.prompt_template_id,
          existingConfig?.prompt_template_id,
          existingConfig?.promptTemplateId,
        ),
        prompt_version_id: firstDefined(
          pt.promptVersionId,
          pt.prompt_version_id,
          existingConfig?.prompt_version_id,
          existingConfig?.promptVersionId,
        ),
        outputFormat:
          firstDefined(pt.outputFormat, pt.output_format) || "string",
        templateFormat:
          firstDefined(pt.templateFormat, pt.template_format) || "mustache",
        modelConfig: {
          model: pt.model || "",
          modelDetail:
            firstDefined(pt.modelDetail, pt.model_detail) ||
            existingConfig?.modelConfig?.modelDetail ||
            {},
          responseFormat: normalizeResponseFormat(responseFormat),
          responseSchema: resolveResponseSchema(
            responseFormat,
            firstDefined(pt.responseSchema, pt.response_schema),
          ),
          toolChoice:
            firstDefined(pt.toolChoice, pt.tool_choice) ??
            existingConfig?.modelConfig?.toolChoice ??
            existingConfig?.model_config?.tool_choice ??
            "auto",
          tools: pt.tools ?? existingConfig?.modelConfig?.tools ?? [],
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
                maxTokens: firstDefined(pt.maxTokens, pt.max_tokens),
                topP: firstDefined(pt.topP, pt.top_p),
                frequencyPenalty: firstDefined(
                  pt.frequencyPenalty,
                  pt.frequency_penalty,
                ),
                presencePenalty: firstDefined(
                  pt.presencePenalty,
                  pt.presence_penalty,
                ),
                tools: pt.tools || [],
                toolChoice: firstDefined(pt.toolChoice, pt.tool_choice),
                response_schema: firstDefined(
                  pt.responseSchema,
                  pt.response_schema,
                ),
                template_format:
                  firstDefined(pt.templateFormat, pt.template_format) ||
                  "mustache",
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
    const refGraphId = firstDefined(
      apiNode.refGraphId,
      apiNode.ref_graph_id,
      existingNode?.data?.graphId,
    );
    const refGraphVersionId = firstDefined(
      apiNode.refGraphVersionId,
      apiNode.ref_graph_version_id,
      existingNode?.data?.version_id,
    );
    const inputMappings = firstDefined(
      apiNode.inputMappings,
      apiNode.input_mappings,
      existingNode?.data?.config?.payload?.inputMappings,
      [],
    );

    return {
      ...existingNode,
      data: {
        ...existingNode?.data,
        label: apiNode.name || existingNode?.data?.label,
        ports: apiNode.ports || existingNode?.data?.ports || [],
        versionId: refGraphVersionId || "",
        graphId: refGraphId || "",
        ref_graph_version_id:
          refGraphVersionId || existingNode?.data?.ref_graph_version_id || "",
        config: {
          ...existingNode?.data?.config,
          graphId: refGraphId || existingNode?.data?.config?.graphId || "",
          versionId:
            refGraphVersionId || existingNode?.data?.config?.version_id || "",
          payload: {
            ...existingNode?.data?.config?.payload,
            inputMappings,
          },
        },
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
