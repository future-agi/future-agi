import { useState, useMemo, useCallback } from "react";
import { useFormContext, useController } from "react-hook-form";
import { MODEL_CONFIG_DEFAULTS } from "src/sections/agent-playground/utils/constants";
import { usePromptNodeQueries } from "./usePromptNodeQueries";
import { useModelParameters } from "./useModelParameters";
import { savePromptNode } from "./promptNodeFormUtils";

const SUPPORTED_AGENT_BUILDER_OUTPUT_FORMATS = new Set(["string", "json"]);

/**
 * Main custom hook for PromptNodeForm component
 * Manages all form state, effects, and handlers
 * @returns {Object} All form state and handlers
 */
export function usePromptNodeForm() {
  const { control, watch, setValue } = useFormContext();

  // Read saved configuration from form's payload field so it updates on version change
  const watchedPayload = watch("payload");
  const savedConfiguration = useMemo(
    () => watchedPayload?.promptConfig?.[0]?.configuration,
    [watchedPayload],
  );

  // Local state
  const [showCreateSchema, setShowCreateSchema] = useState(false);
  const [paramsAnchorEl, setParamsAnchorEl] = useState(null);

  // Watch form values
  const watchedModelConfig = watch("modelConfig");
  const watchedMessages = watch("messages");
  const watchedOutputFormat = watch("outputFormat");
  const isUnsupportedOutputFormat = Boolean(
    watchedOutputFormat &&
      !SUPPORTED_AGENT_BUILDER_OUTPUT_FORMATS.has(watchedOutputFormat),
  );

  const modelConfig = useMemo(
    () => watchedModelConfig || MODEL_CONFIG_DEFAULTS,
    [watchedModelConfig],
  );
  const messages = useMemo(() => watchedMessages || [], [watchedMessages]);

  // Derived values
  const watchedModel = modelConfig?.model;
  const watchedModelProvider = modelConfig?.modelDetail?.providers;
  const isModelSelected = Boolean(watchedModel);

  // Response format controller
  const { field: responseFormatField } = useController({
    name: "modelConfig.responseFormat",
    control,
  });

  // Data fetching
  const {
    responseSchema,
    modelParams,
    responseFormatMenuItems: baseMenuItems,
    isLoading: isLoadingQueries,
  } = usePromptNodeQueries(watchedModel, watchedModelProvider);

  // Ensure the current custom schema always appears in the menu items,
  // even before the response-schema API list has loaded.
  const responseFormatMenuItems = useMemo(() => {
    const currentSchema = modelConfig?.responseSchema;
    const menuItems = [...baseMenuItems];
    if (
      responseFormatField.value === "json_schema" &&
      !menuItems.some((m) => m.value === "json_schema")
    ) {
      menuItems.push({ label: "JSON Schema", value: "json_schema" });
    }
    if (
      currentSchema?.id &&
      !menuItems.some((m) => m.value === currentSchema.id)
    ) {
      menuItems.push({ label: currentSchema.name, value: currentSchema.id });
    }
    return menuItems;
  }, [baseMenuItems, modelConfig?.responseSchema, responseFormatField.value]);

  // Model parameters management
  const {
    modelParameters,
    updateSliderParameter,
    updateBooleanParameter,
    updateDropdownParameter,
    updateReasoningSliderParameter,
    updateReasoningDropdownParameter,
    updateShowReasoningProcess,
  } = useModelParameters(modelParams, watchedModel, savedConfiguration);

  // Popover handlers
  const handleParamsClick = (event) => {
    setParamsAnchorEl(event.currentTarget);
  };

  const handleParamsClose = () => {
    setParamsAnchorEl(null);
  };

  const isParamsPopoverOpen = Boolean(paramsAnchorEl);

  // Form handlers
  const handleModelChange = useCallback(
    (e) => {
      const value = e.target.value;
      setValue("modelConfig", {
        ...modelConfig,
        model: value.modelName || value.model_name,
        modelDetail: value,
        maxTokens: 1000,
      });
    },
    [modelConfig, setValue],
  );

  const handleToolsApply = useCallback(
    (tools) => {
      setValue("modelConfig", {
        ...modelConfig,
        tools,
      });
    },
    [modelConfig, setValue],
  );

  // Build payload for form submission (validation is handled by Zod schema)
  const buildPayload = useCallback(
    (formData) => {
      return savePromptNode(formData, modelParameters, responseSchema);
    },
    [modelParameters, responseSchema],
  );

  return {
    // Loading
    isLoadingQueries,
    // Form context
    control,
    modelConfig,
    messages,
    isModelSelected,
    isUnsupportedOutputFormat,
    responseFormatField,

    // Data
    responseFormatMenuItems,

    // Model parameters
    modelParameters,
    updateSliderParameter,
    updateBooleanParameter,
    updateDropdownParameter,
    updateReasoningSliderParameter,
    updateReasoningDropdownParameter,
    updateShowReasoningProcess,

    // UI state
    showCreateSchema,
    setShowCreateSchema,
    paramsAnchorEl,
    isParamsPopoverOpen,
    handleParamsClick,
    handleParamsClose,

    // Handlers
    handleModelChange,
    handleToolsApply,
    buildPayload,
  };
}
