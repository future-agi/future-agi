import { getRandomId, isUUID } from "src/utils/utils";
import {
  transformParameterType,
  getReasoningFormValues,
  MODEL_TYPES,
} from "../RunPrompt/common";
import _ from "lodash";
import { DEFAULT_MESSAGES } from "./constants";

// Normalize key for matching (remove special chars, lowercase)
const normalizeKey = (key) => {
  return key.toLowerCase().replace(/[-_/]/g, "");
};

// Create mapping between normalized keys and original object keys
const createKeyMapping = (modelParams) => {
  const keyMapping = {};
  Object.keys(modelParams).forEach((key) => {
    keyMapping[normalizeKey(key)] = key;
  });
  return keyMapping;
};

// Escape/unescape dots in model keys to prevent RHF from interpreting them as nested paths
export const escapeModelKey = (key) => key?.replace(/\./g, "__DOT__") ?? key;
export const unescapeModelKey = (key) => key?.replace(/__DOT__/g, ".") ?? key;

// Main function to match and create new modelParams
export const matchModelParams = (item) => {
  const { modelParams, model } = item;

  // If modelParams or model is not available, return original
  if (!modelParams || !model) {
    return modelParams;
  }

  // Create normalized key mapping
  const keyMapping = createKeyMapping(modelParams);

  // Extract model names from the model array
  const modelNames = Array.isArray(model)
    ? model.map((m) => (typeof m === "string" ? m : m?.name)).filter(Boolean)
    : [];

  // Get unique model names
  const uniqueModelNames = _.uniq(modelNames);

  // Create new modelParams with matched keys
  const newModelParams = {};

  uniqueModelNames.forEach((arrayName) => {
    const normalizedArrayName = normalizeKey(arrayName);
    const matchedKey = keyMapping[normalizedArrayName];

    if (matchedKey) {
      // Use escaped key to prevent RHF dot-path splitting
      newModelParams[escapeModelKey(arrayName)] = modelParams[matchedKey];
    }
  });

  return newModelParams;
};

export const getDefaultPromptConfig = () => {
  return {
    id: getRandomId(),
    name: "",
    version: "",
    outputFormat: "string",
    experimentType: "llm",
    messages: DEFAULT_MESSAGES,
    model: [],
    voice: [],
    modelParams: {},
    configuration: {
      tools: [],
      toolChoice: "auto",
    },
  };
};

export function getModelParamsDisplayString(modelParameters) {
  if (!modelParameters || typeof modelParameters !== "object") return "";

  const parts = [];

  // --- Sliders: use label ---
  if (Array.isArray(modelParameters.sliders)) {
    const sliderLabels = modelParameters.sliders.map((item) =>
      _.startCase(_.camelCase(item.label)),
    );
    parts.push(...sliderLabels);
  }

  // --- Response Format: use value ---
  if (Array.isArray(modelParameters.responseFormat)) {
    parts.push("Response Format");
  }

  // --- Booleans: use label ---
  if (Array.isArray(modelParameters.booleans)) {
    const booleanLabels = modelParameters.booleans.map((item) =>
      _.startCase(_.camelCase(item.label)),
    );
    parts.push(...booleanLabels);
  }

  // --- Dropdowns: use label ---
  if (Array.isArray(modelParameters.dropdowns)) {
    const dropdownLabels = modelParameters.dropdowns.map((item) =>
      _.startCase(_.camelCase(item.label)),
    );
    parts.push(...dropdownLabels);
  }

  if (modelParameters?.reasoning) {
    parts.push(_.startCase("reasoning"));
  }

  return parts.join(", ");
}

const _replaceIdWithColumnName = (content, allColumns = []) => {
  // Handle if content is an array
  if (Array.isArray(content)) {
    return content.map((part) => {
      if (part.type === "text" && typeof part.text === "string") {
        let updatedText = part.text;

        allColumns.forEach(({ headerName, field }) => {
          const pattern = new RegExp(
            `{{\\s*${field}((?:\\.[^\\s{}]+)*)\\s*}}`,
            "g",
          );
          updatedText = updatedText.replace(
            pattern,
            (match, rest) => `{{${headerName}${rest || ""}}}`,
          );
        });

        return {
          ...part,
          text: updatedText,
        };
      }

      const MEDIA_TYPES = ["image_url", "pdf_url", "audio_url"];
      if (MEDIA_TYPES.includes(part?.type) && part[part.type]) {
        const original = part[part.type];
        const converted = Object.fromEntries(
          Object.entries(original).map(([key, value]) => [
            _.snakeCase(key),
            value,
          ]),
        );

        return { ...part, [part.type]: converted };
      }

      return part;
    });
  }

  // Handle if content is a string
  if (typeof content === "string") {
    let updatedContent = content;
    allColumns.forEach(({ headerName, field }) => {
      const pattern = new RegExp(`{{\\s*${field}\\s*}}`, "g");
      updatedContent = updatedContent.replace(pattern, `{{${headerName}}}`);
    });

    return [
      {
        type: "text",
        text: updatedContent,
      },
    ];
  }

  // For any other type, just return as is
  return content;
};

const asArray = (value) => (Array.isArray(value) ? value : []);
const normalizePromptConfigItem = (item = {}) => ({
  ...item,
  id: item.id,
  promptId: item.prompt_id ?? null,
  promptVersion: item.prompt_version ?? null,
  promptName: item.prompt_name ?? null,
  agentId: item.agent_id ?? null,
  agentVersion: item.agent_version ?? null,
  model: item.model,
  modelParams: item.model_params ?? {},
  modelConfig: item.model_config,
  messages: item.messages,
  configuration: item.configuration,
  outputFormat: item.output_format,
  voiceInputColumnId: item.voice_input_column_id ?? "",
});


export const getExperimentDefaultValue = (
  editConfigData = {},
  _allColumns = [],
) => {
  const columnId = editConfigData?.column_id ?? null;
  const name = editConfigData?.name || "";
  const normalizedPromptConfigs = asArray(editConfigData?.prompt_configs).map(
    normalizePromptConfigItem,
  );
  const experimentType = editConfigData?.experiment_type || "llm";
  const outputFormat =
    editConfigData?.output_format ||
    normalizedPromptConfigs?.[0]?.outputFormat ||
    "string";
  const userEvalMetrics = asArray(editConfigData?.user_eval_metrics).map(
    (item) => ({
      ...item,
      actualEvalCreatedId: item.id,
      evalId: item.id,
    }),
  );
  // Transform prompt configs using existing function
  const promptConfigTransformed = reversePromptConfigTransform(
    normalizedPromptConfigs,
    experimentType,
  );

  // Transform agent configs to form format
  const agentConfigs = asArray(editConfigData?.agent_configs).map((agent) => ({
    _itemId: agent.id,
    agentId: agent.agent_id,
    agentVersion: agent.agent_version,
    name: agent.agent_name ?? agent.name,
    experimentType: "llm",
    outputFormat: outputFormat || "string",
    promptConfigId: agent?.id,
  }));

  const promptConfig = [...(promptConfigTransformed || []), ...agentConfigs];

  return {
    columnId,
    name,
    outputFormat,
    experimentType,
    promptConfig,
    userEvalMetrics,
  };
};

export const getDefaultPromptConfigByModelType = (experimentType) => {
  const defaultMessages = [
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
  ];

  const baseConfig = {
    modelParams: {},
    configuration: {
      toolChoice: "auto",
      tools: [],
    },
  };

  switch (experimentType) {
    case "llm":
      return {
        ...baseConfig,
        experimentType: "llm",
        promptId: "",
        promptVersion: "",
        agentId: "",
        agentVersion: "",
        model: [],
        outputFormat: "string",
      };

    case "tts":
      return {
        ...baseConfig,
        experimentType: "tts",
        messages: defaultMessages,
        model: [],
        voice: [],
        outputFormat: "audio",
      };

    case "stt":
      return {
        ...baseConfig,
        experimentType: "stt",
        messages: defaultMessages,
        model: [],
        voiceInputColumnId: "",
        outputFormat: "string",
      };

    case "image":
      return {
        ...baseConfig,
        experimentType: "image",
        messages: defaultMessages,
        model: [],
        outputFormat: "image",
      };

    default:
      return {
        ...baseConfig,
        experimentType: "llm",
        promptId: "",
        promptVersion: "",
        agentId: "",
        agentVersion: "",
        model: [],
        outputFormat: "string",
      };
  }
};

export const getLabelFrom = (voiceOptions, voice) => {
  return voiceOptions?.voices?.find((v) => v?.value === voice)?.label;
};

const getTextResponse = (list) => {
  return (
    list?.find((item) => item?.value === "text")?.value ?? list?.[0]?.value
  );
};

export const getModelParamsDefaultValue = (
  modelParams,
  currentModelParams,
  _selectedModel,
) => {
  const transformedModelParamsSliders = modelParams?.sliders
    ?.map((item) => {
      // Exclude any items whose label is "logoUrl" or "providers"
      if (
        _.camelCase(item?.label) === "logoUrl" ||
        _.camelCase(item?.label) === "providers"
      ) {
        return null;
      }
      if (currentModelParams?.[_.camelCase(item?.label)] !== undefined) {
        return {
          ...item,
          id: item?.label,
          value: currentModelParams[_.camelCase(item?.label)] ?? item?.default,
        };
      }
      return {
        ...item,
        id: item?.label,
        value: item?.value ?? item?.default,
      };
    })
    ?.filter(Boolean);

  const sliders = transformedModelParamsSliders;
  const responseFormat = modelParams?.responseFormat;
  const booleans = transformParameterType(
    modelParams?.booleans,
    currentModelParams,
    "booleans",
  );
  const dropdowns = transformParameterType(
    modelParams?.dropdowns,
    currentModelParams,
    "dropdowns",
  );

  const result = {};

  // Only process sliders if they exist
  if (Array.isArray(sliders)) {
    sliders.forEach((param) => {
      // convert label to camelCase
      const key = _.camelCase(param.label);

      // use value if defined, else default if defined, else 0 (preserves null)
      const value =
        param?.value !== undefined
          ? param.value
          : param?.default !== undefined
            ? param.default
            : 0;

      result[key] = value;
    });
  }

  const fin = {
    ...result,
    ...(Array.isArray(booleans) &&
      booleans.length > 0 && {
        booleans: Object.fromEntries(
          booleans.map((item) => [
            item.label,
            item?.value !== undefined
              ? item.value
              : item?.default !== undefined
                ? item.default
                : false,
          ]),
        ),
      }),
    ...(Array.isArray(dropdowns) &&
      dropdowns.length > 0 && {
        dropdowns: Object.fromEntries(
          dropdowns.map((item) => [
            item.label,
            item?.value ?? item.default ?? item?.options?.[0],
          ]),
        ),
      }),
    ...(currentModelParams?.responseFormat
      ? { responseFormat: currentModelParams.responseFormat }
      : responseFormat?.[0]?.value
        ? {
            responseFormat: getTextResponse(responseFormat),
          }
        : {}),
  };

  if (modelParams?.reasoning) {
    fin.reasoning = getReasoningFormValues(
      modelParams.reasoning,
      currentModelParams?.reasoning,
    );
  }

  return fin;
};

export const reversePromptConfigTransform = (data, experimentType) => {
  if (!Array.isArray(data) || data.length === 0) return [];

  const defaultConfig = { toolChoice: "auto", tools: [] };

  if (experimentType === MODEL_TYPES.LLM) {
    const groups = new Map();

    data.forEach((item) => {
      const key = `${item.promptId ?? ""}|${item.promptVersion ?? ""}|${item.agentId ?? ""}|${item.agentVersion ?? ""}`;
      if (!groups.has(key)) {
        groups.set(key, {
          promptId: item.promptId || null,
          promptVersion: item.promptVersion || null,
          agentId: item.agentId || null,
          name: item?.promptName || null,
          agentVersion: item.agentVersion || null,
          model: [],
          modelParams: {},
          experimentType: experimentType,
          configuration: item.configuration || defaultConfig,
          ...(item.outputFormat && { outputFormat: item.outputFormat }),
        });
      }
      const group = groups.get(key);
      const { providers, logoUrl } = item.modelParams || {};
      group.model.push({
        id: item.id,
        value: item.model,
        logoUrl: logoUrl || "",
        providers: providers || "",
        promptConfigId: item.id,
      });
      // Filter out null values from modelParams to ensure complete params
      const cleanModelParams = item.modelParams
        ? Object.fromEntries(
            Object.entries(item.modelParams).filter(
              ([, value]) => value !== null && value !== undefined,
            ),
          )
        : {};
      group.modelParams[item.model] = cleanModelParams;
    });
    return Array.from(groups.values());
  }

  if (experimentType === MODEL_TYPES.TTS) {
    const groups = new Map();

    data.forEach((item) => {
      const messagesKey = JSON.stringify(item.messages || []);
      if (!groups.has(messagesKey)) {
        groups.set(messagesKey, {
          messages: item.messages || [],
          model: [],
          modelParams: {},
          experimentType: experimentType,
          configuration: item.configuration || defaultConfig,
        });
      }
      const group = groups.get(messagesKey);
      const modelName =
        typeof item.model === "string" ? item.model : item.model?.name;
      const voice = item.modelConfig?.voice ?? item.model?.config?.voice;
      const { providers, logoUrl } = item.modelParams || {};

      let modelEntry = group.model.find((m) => m.value === modelName);
      if (!modelEntry) {
        modelEntry = {
          id: item.id,
          value: modelName,
          logoUrl: logoUrl || "",
          providers: providers || "",
          promptConfigId: item.id,
          voices: [],
        };
        group.model.push(modelEntry);
        // Filter out null values from modelParams to ensure complete params
        const cleanModelParams = item.modelParams
          ? Object.fromEntries(
              Object.entries(item.modelParams).filter(
                ([, value]) => value !== null && value !== undefined,
              ),
            )
          : {};
        group.modelParams[modelName] = cleanModelParams;
      }
      if (voice) modelEntry.voices.push({ voice, promptConfigId: item.id });
    });

    return Array.from(groups.values());
  }

  if (experimentType === MODEL_TYPES.STT) {
    const groups = new Map();

    data.forEach((item) => {
      const key = `${item.voiceInputColumnId ?? ""}|${JSON.stringify(item.messages || [])}`;
      if (!groups.has(key)) {
        groups.set(key, {
          messages: item.messages || [],
          model: [],
          voiceInputColumnId: item.voiceInputColumnId || "",
          modelParams: {},
          experimentType: experimentType,
          configuration: item.configuration || defaultConfig,
        });
      }
      const group = groups.get(key);
      const { providers, logoUrl } = item.modelParams || {};
      group.model.push({
        id: item.id,
        value: item.model,
        logoUrl: logoUrl || "",
        providers: providers || "",
        promptConfigId: item.id,
      });
      // Filter out null values from modelParams to ensure complete params
      const cleanModelParams = item.modelParams
        ? Object.fromEntries(
            Object.entries(item.modelParams).filter(
              ([, value]) => value !== null && value !== undefined,
            ),
          )
        : {};
      group.modelParams[item.model] = cleanModelParams;
    });

    return Array.from(groups.values());
  }

  if (experimentType === MODEL_TYPES.IMAGE) {
    const groups = new Map();

    data.forEach((item) => {
      const key = JSON.stringify(item.messages || []);
      if (!groups.has(key)) {
        groups.set(key, {
          messages: item.messages || [],
          model: [],
          modelParams: {},
          experimentType: experimentType,
          configuration: item.configuration || defaultConfig,
        });
      }
      const group = groups.get(key);
      const { providers, logoUrl } = item.modelParams || {};
      group.model.push({
        id: item.id,
        value: item.model,
        logoUrl: logoUrl || "",
        providers: providers || "",
        promptConfigId: item.id,
      });
      // Filter out null values from modelParams to ensure complete params
      const cleanModelParams = item.modelParams
        ? Object.fromEntries(
            Object.entries(item.modelParams).filter(
              ([, value]) => value !== null && value !== undefined,
            ),
          )
        : {};
      group.modelParams[item.model] = cleanModelParams;
    });

    return Array.from(groups.values());
  }

  return [];
};

export const promptConfigTransform = (
  data,
  experimentType,
  outputFormat,
  isEditing = false,
) => {
  const defaultConfig = { toolChoice: "auto", tools: [] };

  const buildModelParams = (modelParams, e) => ({
    providers: e?.providers || "",
    logoUrl: e?.logoUrl || "",
    ...modelParams?.[e?.value],
  });

  if (experimentType === MODEL_TYPES.LLM) {
    return (data ?? []).flatMap((promptConfig) => {
      // Handle agents - no model transformation needed
      if (promptConfig?.agentId) {
        return [
          {
            ...(isEditing &&
              promptConfig?.promptConfigId &&
              isUUID(promptConfig.promptConfigId) && {
                id: promptConfig.promptConfigId,
              }),

            name: promptConfig.name,
            agent_id: promptConfig.agentId,
            agent_version: promptConfig.agentVersion || null,
          },
        ];
      }

      // Handle prompts - transform with models
      const updatedModelParams = { ...promptConfig?.modelParams };
      const models = (promptConfig?.model ?? []).filter((e) => e?.value);
      models.forEach((e) => {
        updatedModelParams[e.value] = buildModelParams(updatedModelParams, e);
      });
      return models.map((e) => ({
        ...(isEditing && e?.id && isUUID(e.id) && { id: e.id }),
        output_format: (promptConfig?.outputFormat || outputFormat) ?? "string",
        prompt_id: promptConfig?.promptId || null,
        prompt_version: promptConfig?.promptVersion || null,
        agent_id: promptConfig?.agentId || null,
        agent_version: promptConfig?.agentVersion || null,
        model: e.value,
        model_params: updatedModelParams[e.value] || {},
        configuration: promptConfig?.configuration || defaultConfig,
      }));
    });
  }

  if (experimentType === MODEL_TYPES.TTS) {
    return (data ?? []).flatMap((promptConfig) => {
      const updatedModelParams = { ...promptConfig?.modelParams };
      const modelEntries = (promptConfig?.model ?? []).reduce((acc, e) => {
        if (!e?.value) return acc;
        updatedModelParams[e.value] = buildModelParams(updatedModelParams, e);
        if (Array.isArray(e?.voices) && e.voices.length > 0) {
          e.voices.forEach((voiceObj) => {
            const voice =
              typeof voiceObj === "string" ? voiceObj : voiceObj.voice;
            const promptConfigId =
              typeof voiceObj === "string"
                ? e?.promptConfigId
                : voiceObj.promptConfigId ?? e?.promptConfigId;
            acc.push({ name: e.value, config: { voice }, promptConfigId });
          });
        }
        return acc;
      }, []);
      return modelEntries.map(({ name, config, promptConfigId }) => ({
        ...(isEditing &&
          promptConfigId &&
          isUUID(promptConfigId) && { id: promptConfigId }),
        output_format: outputFormat ?? "audio",
        messages: promptConfig?.messages || [],
        model: { name, config },
        model_params: updatedModelParams[name] || {},
        configuration: promptConfig?.configuration || defaultConfig,
      }));
    });
  }

  if (experimentType === MODEL_TYPES.STT) {
    return (data ?? []).flatMap((promptConfig) => {
      const updatedModelParams = { ...promptConfig?.modelParams };
      const models = (promptConfig?.model ?? []).filter((e) => e?.value);
      models.forEach((e) => {
        updatedModelParams[e.value] = buildModelParams(updatedModelParams, e);
      });
      return models.map((e) => ({
        ...(isEditing && e?.id && isUUID(e.id) && { id: e.id }),
        output_format: outputFormat ?? "string",
        messages: promptConfig?.messages || [],
        model: e.value,
        voice_input_column_id: promptConfig?.voiceInputColumnId || "",
        model_params: updatedModelParams[e.value] || {},
        configuration: promptConfig?.configuration || defaultConfig,
      }));
    });
  }

  if (experimentType === MODEL_TYPES.IMAGE) {
    return (data ?? []).flatMap((promptConfig) => {
      const updatedModelParams = { ...promptConfig?.modelParams };
      const models = (promptConfig?.model ?? []).filter((e) => e?.value);
      models.forEach((e) => {
        updatedModelParams[e.value] = buildModelParams(updatedModelParams, e);
      });
      return models.map((e) => ({
        ...(isEditing && e?.id && isUUID(e.id) && { id: e.id }),
        output_format: outputFormat ?? "image",
        messages: promptConfig?.messages || [],
        model: e.value,
        model_params: updatedModelParams[e.value] || {},
        configuration: promptConfig?.configuration || defaultConfig,
      }));
    });
  }

  return [];
};
