import _ from "lodash";
import { normalizeForComparison } from "src/sections/workbench/createPrompt/Playground/common";
import { getRandomId } from "src/utils/utils";
import { z } from "zod";

function getIdFromObject(obj) {
  if (
    obj &&
    obj?.content &&
    Array.isArray(obj.content) &&
    obj?.content?.[0]?.text
  ) {
    const match = obj.content[0].text.match(/\{\{(.+?)\}\}/);
    return match ? match[1] : null;
  }
  return null;
}

export const transformDefaultData = (editConfigData, allColumns) => {
  // Default messages must have both system (index 0) and user (index 1)
  // because TTS, STT, and IMAGE model types use messages[1]
  const defaultMsg = [
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

  const replaceIdWithColumnName = (content) => {
    // Handle if content is an array
    if (Array.isArray(content)) {
      return content.map((part) => {
        if (part.type === "text" && typeof part.text === "string") {
          let updatedText = part.text;

          allColumns.forEach(({ headerName, field }) => {
            // Pattern for dot notation (JSON paths): {{field.path.to.value}}
            const dotPattern = new RegExp(
              `{{\\s*${field}((?:\\.[^\\s{}]+)*)\\s*}}`,
              "g",
            );
            updatedText = updatedText.replace(
              dotPattern,
              (match, rest) => `{{${headerName}${rest || ""}}}`,
            );

            // Pattern for bracket notation (images indexed): {{field[0]}}, {{field[1]}}
            const bracketPattern = new RegExp(
              `{{\\s*${field}(\\[\\d+\\])\\s*}}`,
              "g",
            );
            updatedText = updatedText.replace(
              bracketPattern,
              (match, index) => `{{${headerName}${index}}}`,
            );
          });

          return {
            ...part,
            text: updatedText,
          };
        }

        // Handle media content (image, pdf, audio) with nested keys
        const mediaTypes = {
          image_url: "imageUrl",
          pdf_url: "pdfUrl",
          audio_url: "audioUrl",
        };

        if (mediaTypes[part?.type] && part[mediaTypes[part?.type]]) {
          const original = part?.[mediaTypes?.[part?.type]];
          const converted = Object?.fromEntries(
            Object.entries(original)?.map(([key, value]) => [
              _.snakeCase(key),
              value,
            ]),
          );

          return {
            ...part,
            [mediaTypes[part.type]]: converted,
          };
        }

        return part;
      });
    }

    // Handle if content is a string
    if (typeof content === "string") {
      let updatedContent = content;
      allColumns.forEach(({ headerName, field }) => {
        // Pattern for basic column reference: {{field}}
        const basicPattern = new RegExp(`{{\\s*${field}\\s*}}`, "g");
        updatedContent = updatedContent.replace(
          basicPattern,
          `{{${headerName}}}`,
        );

        // Pattern for bracket notation (images indexed): {{field[0]}}, {{field[1]}}
        const bracketPattern = new RegExp(
          `{{\\s*${field}(\\[\\d+\\])\\s*}}`,
          "g",
        );
        updatedContent = updatedContent.replace(
          bracketPattern,
          (match, index) => `{{${headerName}${index}}}`,
        );
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

  // Backend stores both snake_case and camelCase copies on
  // `run_prompt_config`; for some records (e.g. TTS) the camelCase
  // `modelType` is an empty string while snake_case `model_type` carries
  // the real value. Read the snake_case source first, then fall back to
  // camelCase so the form hydrates with the correct type.
  const runPromptConfig =
    editConfigData?.run_prompt_config ?? editConfigData?.runPromptConfig;
  const resolvedModelType =
    runPromptConfig?.model_type || runPromptConfig?.modelType;

  let voiceInputColumn = "";
  if (resolvedModelType === MODEL_TYPES.STT) {
    const userMessage = editConfigData?.messages?.find(
      (msg) => msg?.role === "user",
    );
    voiceInputColumn = getIdFromObject(userMessage);
  }

  return {
    name: editConfigData?.name || "",
    config: {
      model: editConfigData?.model || "gpt-4o",
      run_prompt_config: {
        modelName: editConfigData?.model || "gpt-4o",
        logoUrl: runPromptConfig?.logoUrl,
        providers: runPromptConfig?.providers,
        isAvailable: runPromptConfig?.isAvailable,
        voice: runPromptConfig?.voice || "",
        voiceId: runPromptConfig?.voiceId || "",
      },
      modelType: resolvedModelType || MODEL_TYPES.LLM,
      voiceInputColumn,
      outputFormat: editConfigData?.outputFormat || "string",
      concurrency: editConfigData?.concurrency || "5",
      messages: (() => {
        const msgs = editConfigData?.messages
          ? editConfigData?.messages
              ?.filter((eachMsg) => eachMsg != null)
              ?.map((eachMsg, ind) => {
                if (
                  ind === 0 &&
                  eachMsg?.role === "user" &&
                  editConfigData?.improvePrompt
                ) {
                  eachMsg.content = editConfigData?.improvePrompt;
                }
                // Ensure content is always a valid array
                const transformedContent = replaceIdWithColumnName(
                  eachMsg?.content,
                );
                return {
                  id: getRandomId(),
                  role: eachMsg?.role,
                  content:
                    transformedContent != null
                      ? transformedContent
                      : [{ type: "text", text: "" }],
                };
              })
          : defaultMsg;

        // Ensure we have at least 2 messages for TTS/STT/IMAGE model types
        // which access messages[1] for user input
        if (msgs.length < 2) {
          // Add missing system message at index 0 if needed
          if (msgs.length === 0 || msgs[0]?.role !== "system") {
            msgs.unshift({
              id: getRandomId(),
              role: "system",
              content: [{ type: "text", text: "" }],
            });
          }
          // Add user message at index 1 if needed
          if (msgs.length < 2) {
            msgs.push({
              id: getRandomId(),
              role: "user",
              content: [{ type: "text", text: "" }],
            });
          }
        }

        return msgs;
      })(),
      responseFormat:
        typeof editConfigData?.responseFormat === "object"
          ? editConfigData?.responseFormat?.name
          : editConfigData?.responseFormat,
      // temperature: editConfigData?.temperature,
      // topP: editConfigData?.topP,
      // maxTokens: editConfigData?.maxTokens,
      // presencePenalty: editConfigData?.presencePenalty,
      // frequencyPenalty: editConfigData?.frequencyPenalty,
      prompt: "",
      promptVersion: "",
      // toolChoice: editConfigData?.toolChoice || "none",
      tools: editConfigData?.tools?.map((tool) => tool?.id) || [],
    },
  };
};

/**
 * Extract variables from columns for validation/coloring in the editor.
 * Includes both base column names and JSON paths for JSON-type columns.
 * Also includes indexed access for images columns.
 *
 * @param {Array} allColumns - All columns in the dataset
 * @param {Object} jsonSchemas - JSON schemas keyed by column ID (from useGetJsonColumnSchema)
 * @param {Object} derivedVariables - Derived variables from prompt outputs (from useDerivedVariables)
 * @returns {Object} Map of variable names to placeholder values for validation
 */
export const extractVariableFromAllCols = (
  allColumns = [],
  jsonSchemas = {},
  derivedVariables = {},
) => {
  const res = {};
  if (allColumns?.length > 0) {
    for (let i = 0; i < allColumns.length; i++) {
      const col = allColumns[i];
      // Add base column name
      // Note: ["1"] is a placeholder value used for validation only.
      // The editor checks if a variable has a non-empty value to mark it as valid (green) or invalid (red).
      // The actual value doesn't matter - any non-empty string works.
      if (!res[col?.headerName]) {
        res[col.headerName] = ["1"];
      }

      // Add JSON paths from schema (covers json columns + text columns with JSON values)
      if (jsonSchemas?.[col?.field]?.keys?.length) {
        const schema = jsonSchemas[col?.field];
        schema.keys.forEach((path) => {
          const fullPath = `${col.headerName}.${path}`;
          if (!res[fullPath]) {
            res[fullPath] = ["1"];
          }
        });
      }

      // For images columns, add indexed access based on max_images_count from jsonSchemas
      const imagesSchema = jsonSchemas?.[col?.field];
      if (col?.dataType === "images" && imagesSchema?.maxImagesCount) {
        for (let idx = 0; idx < imagesSchema.maxImagesCount; idx++) {
          const indexedPath = `${col.headerName}[${idx}]`;
          if (!res[indexedPath]) {
            res[indexedPath] = ["1"];
          }
        }
      }
    }
  }

  // Add derived variables from prompt outputs
  if (derivedVariables?.derived_variables) {
    Object.entries(derivedVariables.derived_variables).forEach(
      ([_columnName, data]) => {
        if (data?.full_variables) {
          data.full_variables.forEach((fullPath) => {
            if (!res[fullPath]) {
              res[fullPath] = ["1"];
            }
          });
        }
      },
    );
  }

  return res;
};

/**
 * Get dropdown options from columns, expanding JSON columns with their nested paths
 * and images columns with their indexed access.
 *
 * @param {Array} allColumns - All columns in the dataset
 * @param {Object} jsonSchemas - JSON schemas keyed by column ID (from useGetJsonColumnSchema)
 * @param {Object} derivedVariables - Derived variables from prompt outputs (from useDerivedVariables)
 * @returns {Array} Dropdown options including JSON paths for autocomplete
 */
export const getDropdownOptionsFromCols = (
  allColumns = [],
  jsonSchemas = {},
  derivedVariables = {},
) => {
  const options = [];

  allColumns?.forEach((col) => {
    // Add the base column option
    options.push({
      id: col?.field,
      value: col?.headerName,
      dataType: col?.dataType,
      isJsonPath: false,
    });

    // Add nested paths from schema (covers json columns + text columns with JSON values)
    if (jsonSchemas?.[col?.field]?.keys?.length) {
      const schema = jsonSchemas[col?.field];
      schema.keys.forEach((path) => {
        options.push({
          id: `${col?.field}.${path}`,
          value: `${col?.headerName}.${path}`,
          dataType: "json_path",
          isJsonPath: true,
          parentColumn: col?.headerName,
        });
      });
    }

    // If this is an images column with max_images_count, add indexed options
    const imagesSchema = jsonSchemas?.[col?.field];
    if (col?.dataType === "images" && imagesSchema?.maxImagesCount) {
      for (let idx = 0; idx < imagesSchema.maxImagesCount; idx++) {
        options.push({
          id: `${col?.field}[${idx}]`,
          value: `${col?.headerName}[${idx}]`,
          dataType: "images_index",
          isImagesIndex: true,
          parentColumn: col?.headerName,
        });
      }
    }
  });

  // Add derived variables from prompt outputs
  if (derivedVariables?.derived_variables) {
    Object.entries(derivedVariables.derived_variables).forEach(
      ([columnName, data]) => {
        if (data?.full_variables) {
          data.full_variables.forEach((fullPath) => {
            options.push({
              id: fullPath,
              value: fullPath,
              dataType: "derived",
              isJsonPath: true,
              isDerived: true,
              parentColumn: columnName,
            });
          });
        }
      },
    );
  }

  return options;
};

/**
 * Find invalid variables in a template text.
 * Validates against column names, JSON paths, and derived variables.
 * and indexed access for images-type columns.
 *
 * @param {string} text - Template text containing {{variable}} placeholders
 * @param {Array} allColumns - All columns in the dataset
 * @param {Object} jsonSchemas - JSON schemas keyed by column ID (optional)
 * @param {Object} derivedVariables - Derived variables from prompt outputs (optional)
 * @returns {Array} List of invalid variable names
 */
export function findInvalidVariables(
  text,
  allColumns = [],
  jsonSchemas = {},
  derivedVariables = {},
) {
  if (!text || !allColumns?.length) return [];

  const invalids = [];
  const variablePattern = /{{\s*([^{}]+?)\s*}}/g;
  const matches = [...text.matchAll(variablePattern)];

  // Helper for case-insensitive comparison with null safety
  const normalize = (str) => {
    if (!str) return "";
    return normalizeForComparison(String(str)).toLowerCase();
  };

  // Build a set of valid derived variable paths for quick lookup
  const derivedPaths = new Set();
  if (derivedVariables?.derived_variables) {
    Object.values(derivedVariables.derived_variables).forEach((data) => {
      if (data?.full_variables) {
        data.full_variables.forEach((path) => {
          derivedPaths.add(normalize(path));
        });
      }
    });
  }

  matches.forEach((match) => {
    const rawVar = match[1]?.trim();
    if (!rawVar) return;

    const normalizedTarget = normalize(rawVar);

    // Check if it's a direct column match (case-insensitive)
    const isDirectColumn = allColumns.some(
      (col) => normalize(col?.headerName) === normalizedTarget,
    );

    if (isDirectColumn) return;

    // Check if it's a derived variable
    if (derivedPaths.has(normalizedTarget)) return;

    // Check if it's a valid JSON path (columnName.path.to.value)
    const dotIndex = rawVar.indexOf(".");
    if (dotIndex > 0) {
      const baseColumn = rawVar.substring(0, dotIndex);

      // Find the column by headerName (case-insensitive)
      const column = allColumns.find(
        (col) => normalize(col?.headerName) === normalize(baseColumn),
      );

      if (column && (column.dataType === "json" || jsonSchemas?.[column.field]?.keys?.length)) {
        // Column has nested paths (json type or text with JSON values) — allow any path
        return;
      }

      // Check if it's a derived variable base column (output from prompt runs)
      // Allow any path starting with a known derived variable column
      if (derivedVariables?.derived_variables?.[baseColumn]) {
        return;
      }
    }

    // Check if it's a valid images indexed access (columnName[index])
    const bracketMatch = rawVar.match(/^(.+?)\[(\d+)\]$/);
    if (bracketMatch) {
      const baseColumn = bracketMatch[1];
      const index = parseInt(bracketMatch[2], 10);

      // Find the column by headerName (case-insensitive)
      const column = allColumns.find(
        (col) => normalize(col?.headerName) === normalize(baseColumn),
      );

      if (column?.dataType === "images") {
        // Images column found - check if index is within maxImagesCount
        const imagesSchema = jsonSchemas?.[column?.field];
        if (
          imagesSchema?.maxImagesCount &&
          index < imagesSchema.maxImagesCount
        ) {
          return;
        }
        // Even if index exceeds maxImagesCount, allow it (will resolve to empty at runtime)
        return;
      }
    }

    invalids.push(rawVar);
  });

  return invalids;
}

export const TextContent = z.object({
  type: z.literal("text"),
  text: z.string(),
});

export const ImageContent = z.object({
  type: z.literal("image_url"),
  imageUrl: z.object({
    img_name: z.string().optional(),
    url: z.string(),
    img_size: z.number().optional(),
  }),
});

export const PdfContent = z.object({
  type: z.literal("pdf_url"),
  pdfUrl: z.object({
    pdf_name: z.string().optional(),
    file_name: z.string().optional(),
    url: z.string(),
    pdf_size: z.number().optional(),
  }),
});

export const AudioContent = z.object({
  type: z.literal("audio_url"),
  audioUrl: z.object({
    audio_name: z.string().optional(),
    url: z.string(),
    audio_size: z.number().optional(),
  }),
});

export const MODEL_TYPES = {
  LLM: "llm",
  STT: "stt",
  TTS: "tts",
  IMAGE: "image",
};

/**
 * Get the output format based on model type.
 * Centralizes the logic for determining output format from model type.
 *
 * @param {string} modelType - The model type (llm, tts, stt, image)
 * @returns {string} The output format (string, audio, image)
 */
export const getOutputFormatForModelType = (modelType) => {
  if (modelType === MODEL_TYPES.TTS) return "audio";
  if (modelType === MODEL_TYPES.IMAGE) return "image";
  return "string";
};

export const DUMMY_MODEL_PARAMS = [
  {
    id: "temperature",
    label: "Temperature",
    min: 0,
    max: 1,
    step: 0.1,
    default: 0.7,
  },
  {
    id: "topP",
    label: "Top P",
    min: 0,
    max: 1,
    step: 0.05,
    default: 1,
  },
  {
    id: "presencePenalty",
    label: "Presence Penalty",
    min: -2,
    max: 2,
    step: 0.1,
    default: 0,
  },
  {
    id: "frequencyPenalty",
    label: "Frequency Penalty",
    min: -2,
    max: 2,
    step: 0.1,
    default: 0,
  },
  {
    id: "maxTokens",
    label: "Max Tokens",
    min: 1,
    max: 4096,
    step: 1,
    default: 1024,
  },
  {
    id: "repetitionPenalty",
    label: "Repetition Penalty",
    min: 0,
    max: 2,
    step: 0.1,
    default: 1,
  },
  {
    id: "beamWidth",
    label: "Beam Width",
    min: 1,
    max: 10,
    step: 1,
    default: 5,
  },
];

/**
 * Replace template variables with their corresponding field names.
 * Handles direct column matches, JSON dot notation, and images indexed access.
 *
 * @param {string} text - Template text containing {{variable}} placeholders
 * @param {Array} matches - Regex matches from variablePattern
 * @param {Array} allColumns - All columns in the dataset
 * @returns {Object} { text: string, invalidVariables: string[] }
 */
export const replaceVariablesWithFields = (text, matches, allColumns, jsonSchemas = {}) => {
  let updatedText = text;
  const invalidVariables = [];

  matches.forEach((match) => {
    const rawVar = match[1].trim();

    // Check for direct column match
    const column = allColumns.find(
      ({ headerName }) =>
        normalizeForComparison(headerName).toLowerCase() ===
        normalizeForComparison(rawVar).toLowerCase(),
    );

    if (column) {
      const replacePattern = new RegExp(`{{\\s*${rawVar}\\s*}}`, "g");
      updatedText = updatedText.replace(replacePattern, `{{${column.field}}}`);
      return;
    }

    // Check for JSON dot notation (e.g., input.prompt)
    const dotIndex = rawVar.indexOf(".");
    if (dotIndex > 0) {
      const baseColumn = rawVar.substring(0, dotIndex);
      const jsonPath = rawVar.substring(dotIndex + 1);
      const jsonColumn = allColumns.find(
        ({ headerName }) =>
          normalizeForComparison(headerName).toLowerCase() ===
          normalizeForComparison(baseColumn).toLowerCase(),
      );

      if (jsonColumn && (jsonColumn.dataType === "json" || jsonSchemas?.[jsonColumn.field]?.keys?.length)) {
        const replacePattern = new RegExp(
          `{{\\s*${rawVar.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*}}`,
          "g",
        );
        updatedText = updatedText.replace(
          replacePattern,
          `{{${jsonColumn.field}.${jsonPath}}}`,
        );
        return;
      }
      invalidVariables.push(rawVar);
      return;
    }

    // Check for images indexed access (e.g., images[0])
    const bracketMatch = rawVar.match(/^(.+?)\[(\d+)\]$/);
    if (bracketMatch) {
      const baseColumn = bracketMatch[1];
      const index = bracketMatch[2];
      const imagesColumn = allColumns.find(
        ({ headerName }) =>
          normalizeForComparison(headerName).toLowerCase() ===
          normalizeForComparison(baseColumn).toLowerCase(),
      );

      if (imagesColumn?.dataType === "images") {
        const replacePattern = new RegExp(
          `{{\\s*${rawVar.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*}}`,
          "g",
        );
        updatedText = updatedText.replace(
          replacePattern,
          `{{${imagesColumn.field}[${index}]}}`,
        );
        return;
      }
      invalidVariables.push(rawVar);
      return;
    }

    invalidVariables.push(rawVar);
  });

  return { text: updatedText, invalidVariables };
};

export const transformParameterType = (params, initialData, paramType) => {
  if (!Array.isArray(params)) return [];

  return params.map((item) => {
    const id = _.camelCase(item?.label);
    const initialValue = initialData?.[paramType]?.[id];

    return {
      ...item,
      id,
      value: initialValue ?? item?.value ?? item?.default ?? null,
    };
  });
};

export const arrayToLabelValueMap = (arr) =>
  arr?.reduce(
    (acc, item) => ({ ...acc, [_.camelCase(item?.label)]: item?.value }),
    {},
  ) ?? {};

// Builds array-based reasoning state for RunPrompt (state-based flow)
export const getDefaultReasoningState = (reasoningConfig) => {
  if (!reasoningConfig) return null;
  return {
    sliders:
      reasoningConfig?.sliders?.map((s) => ({
        ...s,
        id: _.camelCase(s?.label),
        value: s?.default !== undefined ? s.default : null,
      })) ?? [],
    dropdowns:
      reasoningConfig?.dropdowns?.map((d) => ({
        ...d,
        id: _.camelCase(d?.label),
        value: d?.default ?? null,
      })) ?? [],
    showReasoningProcess: true,
  };
};

// Builds object-based reasoning form values for control-based flows (Experiment/Workbench)
export const getReasoningFormValues = (reasoningConfig, savedValues) => {
  if (!reasoningConfig) return {};
  return {
    sliders: Object.fromEntries(
      (reasoningConfig?.sliders ?? []).map((s) => {
        const key = _.camelCase(s?.label);
        return [
          key,
          savedValues?.sliders?.[key] !== undefined
            ? savedValues.sliders[key]
            : s?.default !== undefined
              ? s.default
              : null,
        ];
      }),
    ),
    dropdowns: Object.fromEntries(
      (reasoningConfig?.dropdowns ?? []).map((d) => [
        _.camelCase(d?.label),
        savedValues?.dropdowns?.[_.camelCase(d?.label)] !== undefined
          ? savedValues.dropdowns[_.camelCase(d?.label)]
          : d?.default ?? null,
      ]),
    ),
    showReasoningProcess: savedValues?.showReasoningProcess ?? true,
  };
};
