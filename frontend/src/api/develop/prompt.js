import {
  useInfiniteQuery,
  useMutation,
  useQuery,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const PAGE_SIZE = 10;

export const useDeletePromptTemplate = (options = {}) =>
  useMutation({
    mutationFn: (id) =>
      axios.delete(endpoints.develop.runPrompt.promptTemplateId(id)),
    ...options,
  });

export const usePromptExecutions = (open, search = "", modality = "all") => {
  return useInfiniteQuery({
    queryKey: ["prompts", search, modality],
    enabled: !!open,
    queryFn: async ({ pageParam = 1 }) => {
      const res = await axios.get(
        endpoints.develop.runPrompt.promptExecutions(),
        {
          params: { page: pageParam, search, page_size: PAGE_SIZE, modality },
        },
      );
      return res.data;
    },
    getNextPageParam: (lastPage) => {
      const currentPage = lastPage?.current_page;
      const totalPages = lastPage?.total_pages;
      if (currentPage < totalPages) {
        return currentPage + 1;
      }
      return null;
    },
    initialPageParam: 1,
  });
};

export const usePromptVersions = (selectedPromptId) => {
  return useInfiniteQuery({
    queryKey: ["versions", selectedPromptId],
    enabled: !!selectedPromptId,
    queryFn: async ({ pageParam = 1 }) => {
      const res = await axios.get(
        endpoints.develop.runPrompt.getPromptVersions(),
        {
          params: {
            page: pageParam,
            page_size: PAGE_SIZE,
            template_id: selectedPromptId,
          },
        },
      );
      return res?.data;
    },
    getNextPageParam: (lastPage) => {
      const currentPage = lastPage?.current_page;
      const totalPages = lastPage?.total_pages;
      if (currentPage < totalPages) {
        return currentPage + 1;
      }
      return null;
    },
    initialPageParam: 1,
  });
};

export const useModelParams = (model, provider, modelType) => {
  return useQuery({
    queryKey: ["model-params", model, provider, modelType],
    queryFn: () =>
      axios.get(endpoints.develop.modelParams, {
        params: { model, provider, model_type: modelType },
      }),
    enabled: !!(model && provider && modelType),
    select: (d) => d.data?.result,
  });
};

const buildPromptDraftBody = ({ configuration, messages, variableNames }) => ({
  name: "",
  prompt_config: [{ configuration, messages }],
  ...(variableNames && Object.keys(variableNames).length > 0
    ? { variable_names: variableNames }
    : {}),
});

export const useCreatePromptDraft = (options = {}) =>
  useMutation({
    ...options,
    mutationFn: (params) =>
      axios.post(
        endpoints.develop.runPrompt.createPromptDraft,
        buildPromptDraftBody(params),
      ),
  });
