import _ from "lodash";
import { getReasoningFormValues } from "src/sections/develop-detail/RunPrompt/common";
import { extractJinjaVariables } from "src/utils/jinjaVariables";

// Helper function to normalize for comparison only
export const normalizeForComparison = (variable) => {
  return variable
    .replace(/\u00A0/g, " ") // Convert non-breaking spaces to regular spaces
    .replace(/\u2000-\u200B/g, " ") // Convert various Unicode spaces to regular spaces
    .trim(); // Remove leading/trailing spaces
};

export const extractVariables = (content, templateFormat) => {
  if (!Array.isArray(content) || content?.length === 0) {
    return [];
  }

  // Use Map to track normalized -> original mapping
  const variableMap = new Map();

  for (const item of content) {
    if (item.type === "text") {
      if (templateFormat === "jinja") {
        // Jinja2 AST-based extraction (excludes loop/set scoped vars)
        extractJinjaVariables(item.text).forEach((v) => {
          const normalized = normalizeForComparison(v);
          if (normalized.length > 0 && !variableMap.has(normalized)) {
            variableMap.set(normalized, v);
          }
        });
      } else {
        const match = item.text.match(/{{(.*?)}}/g);
        if (match) {
          match
            .map((v) => v.replace(/{{|}}/g, "").trim())
            .filter((v) => v.length > 0)
            .forEach((v) => {
              const normalized = normalizeForComparison(v);
              if (normalized.length > 0) {
                // Store original value, but use normalized as key to avoid duplicates
                if (!variableMap.has(normalized)) {
                  variableMap.set(normalized, v); // Store the first occurrence (original formatting)
                }
              }
            });
        }
      }
    }
  }

  // Return original values (preserving formatting)
  return Array.from(variableMap.values());
};

export function isContentNotEmpty(contentArray) {
  if (!Array.isArray(contentArray) || contentArray.length === 0) {
    return false;
  }

  return contentArray?.some((item) => {
    if (item.type === "text") {
      return item.text.trim() !== "";
    }
    if (item.type === "image_url") {
      return item.imageUrl?.url?.trim() !== "";
    }
    if (item.type === "audio_url") {
      return item.audioUrl?.url?.trim() !== "";
    }
    if (item.type === "pdf_url") {
      return item.pdfUrl?.url?.trim() !== "";
    }
    return false;
  });
}

export function transformModelParams(modelParams) {
  const { sliders, ...rest } = modelParams || {};
  const transformedSliders = sliders?.map((item) => {
    return {
      ...item,
      id: _.camelCase(item?.label),
      label: item?.label,
    };
  });
  return {
    sliders: transformedSliders ?? [],
    ...rest,
  };
}

export function getModelParamValues(
  defaultValues,
  modelParamsValues,
  useDefaultValues = false,
  _newModelConfig = {},
) {
  // Null checks for inputs
  if (!defaultValues || typeof defaultValues !== "object") {
    return {};
  }

  if (!modelParamsValues || typeof modelParamsValues !== "object") {
    modelParamsValues = {};
  }

  const result = {};

  // Handle sliders
  if (defaultValues.sliders && Array.isArray(defaultValues.sliders)) {
    for (let i = 0; i < defaultValues.sliders.length; i++) {
      const param = defaultValues.sliders[i];

      // Check if param and param.id exist
      if (!param || !param.id) {
        continue;
      }

      const key = param.id;

      // Use value from modelParamsValues if it exists and is valid, otherwise use default
      if (
        !useDefaultValues &&
        key in modelParamsValues &&
        modelParamsValues[key] !== null &&
        modelParamsValues[key] !== undefined
      ) {
        result[key] = modelParamsValues[key];
      } else {
        result[key] = param.default;
      }
    }
  }

  // Handle responseFormat - use first index if not present in modelParamsValues
  if (
    defaultValues.responseFormat &&
    Array.isArray(defaultValues.responseFormat)
  ) {
    if (
      !useDefaultValues &&
      "responseFormat" in modelParamsValues &&
      modelParamsValues.responseFormat !== null &&
      modelParamsValues.responseFormat !== undefined
    ) {
      result.responseFormat = modelParamsValues.responseFormat;
    } else if (defaultValues?.responseFormat?.length > 0) {
      // First, try to find an item with value "text"
      const textFormat = defaultValues?.responseFormat?.find(
        (item) => item?.value === "text",
      );

      if (textFormat?.value) {
        result.responseFormat = textFormat.value;
      } else if (defaultValues?.responseFormat?.[0]?.value) {
        // Fall back to first index if "text" not found
        result.responseFormat = defaultValues?.responseFormat?.[0]?.value;
      }
    }
  }

  // Handle booleans
  if (
    Array.isArray(defaultValues?.booleans) &&
    defaultValues.booleans.length > 0
  ) {
    result.booleans = Object.fromEntries(
      defaultValues.booleans.map((item) => [
        item?.label,
        !useDefaultValues &&
        modelParamsValues?.booleans?.[item?.label] !== undefined
          ? modelParamsValues.booleans[item.label]
          : item?.value !== undefined
            ? item.value
            : item?.default !== undefined
              ? item.default
              : false,
      ]),
    );
  }

  // Handle dropdowns
  if (
    Array.isArray(defaultValues?.dropdowns) &&
    defaultValues.dropdowns.length > 0
  ) {
    result.dropdowns = Object.fromEntries(
      defaultValues.dropdowns.map((item) => [
        item?.label,
        !useDefaultValues &&
        modelParamsValues?.dropdowns?.[item?.label] !== undefined
          ? modelParamsValues.dropdowns[item.label]
          : item?.value ?? item?.default ?? item?.options?.[0],
      ]),
    );
  }

  // Handle reasoning
  if (defaultValues?.reasoning) {
    if (
      !useDefaultValues &&
      modelParamsValues?.reasoning &&
      typeof modelParamsValues.reasoning === "object"
    ) {
      result.reasoning = modelParamsValues.reasoning;
    } else {
      result.reasoning = getReasoningFormValues(defaultValues.reasoning);
    }
  }

  return {
    ...result,
    maxTokens: result?.maxTokens,
    tools: modelParamsValues?.tools ?? [],
    tool_choice: modelParamsValues?.tool_choice ?? "",
  };
}
export const PROMPT_RESULT_TYPES = {
  RAW: "raw",
  MARKDOWN: "markdown",
};

export const PROMPT_RESULT_TYPE_OPTIONS = [
  { label: "Raw", value: PROMPT_RESULT_TYPES.RAW },
  { label: "Markdown", value: PROMPT_RESULT_TYPES.MARKDOWN },
];
