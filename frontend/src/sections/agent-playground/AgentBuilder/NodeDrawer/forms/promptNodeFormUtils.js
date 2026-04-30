import {
  normalizeResponseFormat,
  extractResponseSchema,
  resolveResponseFormatForApi,
  KNOWN_FORMAT_VALUES,
} from "../nodeFormUtils";

import { extractJinjaVariables } from "src/utils/jinjaVariables";

/**
 * Extract variables from content blocks
 * @param {Array} content - Array of content blocks
 * @param {string} [templateFormat] - "jinja" for Jinja2 AST extraction, otherwise regex
 * @returns {Array} Array of variable names found in the content
 */
export const extractVariablesFromContent = (content, templateFormat) => {
  if (!Array.isArray(content)) return [];

  if (templateFormat === "jinja") {
    // Collect all text, then use Jinja2 AST extraction
    const allText = content
      .filter((block) => block.type === "text" && block.text)
      .map((block) => block.text)
      .join("\n");
    return extractJinjaVariables(allText);
  }

  // Default: mustache {{ }} regex
  const variables = new Set();
  const variablePattern = /{{\s*([^{}]+?)\s*}}/g;

  content.forEach((block) => {
    if (block.type === "text" && block.text) {
      let match;
      const text = block.text;
      while ((match = variablePattern.exec(text)) !== null) {
        const variableName = match[1].trim();
        if (variableName) {
          variables.add(variableName);
        }
      }
    }
  });

  return Array.from(variables);
};

/**
 * Builds the payload for prompt node form submission (PATCH).
 * Ports are NOT included — BE auto-creates/reconciles ports from {{variables}} in messages.
 * @param {Object} formData - Form data
 * @param {Object} modelParameters - Model parameters state
 * @returns {Object} Payload object
 */
export function buildPromptNodePayload(
  formData,
  modelParameters,
  responseSchemaList,
) {
  // Map known slider ids to explicit top-level keys
  const KNOWN_SLIDER_KEYS = [
    "temperature",
    "maxTokens",
    "topP",
    "presencePenalty",
    "frequencyPenalty",
  ];
  const sliderValues = {};
  KNOWN_SLIDER_KEYS.forEach((key) => {
    const slider = modelParameters?.sliders?.find((s) => s.id === key);
    sliderValues[key] = slider?.value ?? null;
  });

  // Build reasoning object
  const reasoning = modelParameters?.reasoning
    ? {
        sliders:
          modelParameters.reasoning.sliders?.reduce(
            (acc, item) => ({ ...acc, [item.id]: item.value }),
            {},
          ) || {},
        dropdowns:
          modelParameters.reasoning.dropdowns?.reduce(
            (acc, item) => ({ ...acc, [item.id]: item.value }),
            {},
          ) || {},
        showReasoningProcess:
          modelParameters.reasoning.showReasoningProcess ?? true,
      }
    : { sliders: {}, dropdowns: {}, showReasoningProcess: true };

  // Resolve responseFormat: use full schema object for custom schemas
  const rawFormat = formData.modelConfig.responseFormat || "text";
  const isCustomFormat = !KNOWN_FORMAT_VALUES.includes(rawFormat);
  const resolvedFormat =
    isCustomFormat && responseSchemaList
      ? responseSchemaList.find((s) => s.id === rawFormat) || rawFormat
      : rawFormat;

  // Build configuration object matching workbench format
  const configuration = {
    model: formData.modelConfig.model,
    modelDetail: formData.modelConfig.modelDetail,
    outputFormat: "string",
    toolChoice: formData.modelConfig.toolChoice || "",
    tools: formData.modelConfig.tools || [],
    modelType: "llm",
    ...sliderValues,
    responseFormat: resolvedFormat,
    reasoning,
    template_format: formData.templateFormat || "mustache",
  };

  // Build payload matching workbench format
  const payload = {
    name: formData.name,
    variable_names: {},
    placeholders: {},
    promptConfig: [
      {
        messages: formData.messages.map((m) => ({
          role: m.role,
          content: m.content,
        })),
        configuration,
        placeholders: [],
      },
    ],
    isRun: true,
    evaluationConfigs: [],
    version: formData.version || "",
  };

  return payload;
}

/**
 * Saves prompt node form data (validation is handled by Zod schema)
 * @param {Object} formData - Validated form data
 * @param {Object} modelParameters - Model parameters state
 * @returns {Object} Payload object
 */
export function savePromptNode(formData, modelParameters, responseSchemaList) {
  return buildPromptNodePayload(formData, modelParameters, responseSchemaList);
}

/**
 * Transforms store-shaped nodeUpdate into contract-shaped PATCH payload.
 * @param {Object} nodeUpdate - { label, config: { messages, modelConfig, payload, ... } }
 * @param {Object} config - node.data.config from store (has prompt_template_id, prompt_version_id)
 * @returns {Object} Contract-shaped payload for PATCH /nodes/{id}/
 */
export function buildPatchPayload(nodeUpdate, config) {
  const patch = {};

  if (nodeUpdate.label) patch.name = nodeUpdate.label;

  if (nodeUpdate.config?.messages) {
    const cfg = nodeUpdate.config;
    const promptPayloadConfig = cfg.payload?.promptConfig?.[0]?.configuration;

    patch.prompt_template = {
      prompt_template_id: config?.prompt_template_id,
      prompt_version_id: config?.prompt_version_id,
      messages: cfg.messages.map((m) => ({
        id: m.id,
        role: m.role,
        content: Array.isArray(m.content)
          ? m.content
          : [{ type: "text", text: m.content || "" }],
      })),
      model: cfg.modelConfig?.model || null,
      model_detail: cfg.modelConfig?.modelDetail || null,
      response_format:
        promptPayloadConfig?.response_format ??
        resolveResponseFormatForApi(cfg.modelConfig),
      output_format: "string",
      temperature: promptPayloadConfig?.temperature ?? null,
      max_tokens: promptPayloadConfig?.max_tokens ?? null,
      top_p: promptPayloadConfig?.top_p ?? null,
      frequency_penalty: promptPayloadConfig?.frequencyPenalty ?? null,
      presence_penalty: promptPayloadConfig?.presencePenalty ?? null,
      tools: promptPayloadConfig?.tools || cfg.modelConfig?.tools || [],
      tool_choice:
        promptPayloadConfig?.tool_choice || cfg.modelConfig?.toolChoice || "",
      template_format: promptPayloadConfig?.template_format || cfg.templateFormat || "mustache",
      save_prompt_version: false,
    };
  }

  return patch;
}

/**
 * Maps a PATCH response (contract shape) back into store node.data shape.
 * BE returns: { id, name, config, position, prompt_template: {...}, ports: [...] }
 * Store expects: { label, ports, config: { prompt_template_id, prompt_version_id, modelConfig, messages, ... } }
 *
 * @param {Object} response - Full node object from PATCH response
 * @returns {Object} Store-compatible node.data partial (for updateNodeData)
 */
export function mapPatchResponseToStoreData(response) {
  if (!response) return {};

  const pt = response.prompt_template;
  const storeData = {
    label: response.name,
    ports: (response.ports || []).map((p) => ({
      id: p.id,
      key: p.key,
      display_name: p.display_name,
      direction: p.direction,
      data_schema: p.dataSchema || p.data_schema || {},
      required: p.required,
    })),
  };

  if (pt) {
    storeData.config = {
      prompt_template_id: pt.prompt_template_id,
      prompt_version_id: pt.prompt_version_id,
      templateFormat: pt.template_format || "mustache",
      modelConfig: {
        model: pt.model || "",
        modelDetail: pt.model_detail || {},
        responseFormat: normalizeResponseFormat(pt.response_format),
        responseSchema: extractResponseSchema(pt.response_format),
        toolChoice: pt.tool_choice || "auto",
        tools: pt.tools || [],
      },
      messages: (pt.messages || []).map((m, idx) => ({
        id: `msg-${idx}`,
        role: m.role,
        content:
          typeof m.content === "string"
            ? [{ type: "text", text: m.content }]
            : m.content || [{ type: "text", text: "" }],
      })),
      payload: {
        promptConfig: [
          {
            configuration: {
              temperature: pt.temperature,
              maxTokens: pt.max_tokens,
              topP: pt.top_p,
              frequencyPenalty: pt.frequency_penalty,
              presencePenalty: pt.presence_penalty,
              tools: pt.tools || [],
              toolChoice: pt.tool_choice || "auto",
              template_format: pt.template_format || "mustache",
            },
          },
        ],
      },
    };
  }

  return storeData;
}
