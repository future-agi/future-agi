import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { DEFAULT_RESPONSE_FORMAT_OPTIONS } from "src/sections/agent-playground/utils/constants";
import { buildResponseFormatMenu } from "src/utils/responseFormat";

/**
 * Custom hook for fetching data related to prompt node form
 * @param {string} watchedModel - Selected model name
 * @param {string} watchedModelProvider - Selected model provider
 * @returns {Object} Query results and derived data
 */
export function usePromptNodeQueries(watchedModel, watchedModelProvider) {
  // Fetch response schema for custom output types
  const { data: responseSchema, isLoading: isLoadingResponseSchema } = useQuery(
    {
      queryKey: ["response-schema"],
      queryFn: () => axios.get(endpoints.develop.runPrompt.responseSchema),
      select: (d) => d.data?.results,
      staleTime: 1 * 60 * 1000,
    },
  );

  // Fetch dynamic model params based on selected model
  const { data: modelParams } = useQuery({
    queryKey: ["model-params", watchedModel, "llm", watchedModelProvider],
    queryFn: () =>
      axios.get(endpoints.develop.modelParams, {
        params: {
          model: watchedModel,
          provider: watchedModelProvider,
          model_type: "llm",
        },
      }),
    enabled: !!(watchedModel && watchedModelProvider),
    select: (d) => d.data?.result,
  });

  // Build menu items for response format dropdown
  const responseFormatMenuItems = useMemo(
    () =>
      buildResponseFormatMenu({
        defaults: DEFAULT_RESPONSE_FORMAT_OPTIONS,
        responseSchema,
        modelResponseFormat: modelParams?.responseFormat,
      }),
    [responseSchema, modelParams?.responseFormat],
  );

  return {
    responseSchema,
    modelParams,
    responseFormatMenuItems,
    isLoading: isLoadingResponseSchema,
  };
}
