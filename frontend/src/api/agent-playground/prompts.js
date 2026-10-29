import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { getNextLibraryTemplatePageParam } from "./libraryTemplateResponseUtils";

/**
 * Hook for fetching prompt templates filtered by chat modality.
 * Uses the same query key prefix as the workbench so cache stays in sync.
 * @param {string} search - Search query to filter by name
 * @param {object} options - Additional react-query options
 */
export const useGetPromptTemplates = (search, options = {}) =>
  useQuery({
    queryKey: ["prompt-templates", "chat", search],
    queryFn: ({ signal }) =>
      axios.get(endpoints.develop.runPrompt.createTemplateId, {
        params: {
          // modality: "chat",
          ...(search && { name: search }),
          page_size: 10,
        },
        signal,
      }),
    select: (res) => res.data?.results ?? [],
    staleTime: 30 * 1000,
    ...options,
  });

/**
 * Hook for fetching versions of a prompt template, filtered by chat modality.
 * Uses the same "prompt-versions" key prefix as the workbench so cache stays in sync.
 * @param {string} templateId - The prompt template ID
 * @param {object} options - Additional react-query options
 */
export const useGetPromptVersions = (templateId, options = {}) =>
  useQuery({
    queryKey: ["prompt-versions", templateId],
    queryFn: ({ signal }) =>
      axios.get(endpoints.develop.runPrompt.getPromptVersions(), {
        params: {
          template_id: templateId,
        },
        signal,
      }),
    select: (res) => res.data?.results ?? [],
    staleTime: 30 * 1000,
    enabled: !!templateId,
    ...options,
  });

/**
 * Fetch a single prompt version by ID.
 * Used to ensure the selected version always appears in the dropdown
 * even if its page hasn't been loaded yet via infinite scroll.
 * @param {string} versionId - The prompt version ID
 * @param {object} options - Additional react-query options
 */
export const useGetPromptVersionDetail = (versionId, options = {}) =>
  useQuery({
    queryKey: ["prompt-version-detail", versionId],
    queryFn: ({ signal }) =>
      axios.get(
        `${endpoints.develop.runPrompt.getPromptVersions()}${versionId}/`,
        { signal },
      ),
    select: (res) => res.data,
    staleTime: 30 * 1000,
    enabled: !!versionId,
    ...options,
  });

/**
 * Infinite-scroll variant of useGetPromptTemplates.
 * Used by PromptNodePopper prompt list (paginated, 10 per page).
 * @param {string} search - Search query to filter by name
 * @param {object} options - Additional react-query options
 */
export const useGetPromptTemplatesInfinite = (search, options = {}) =>
  useInfiniteQuery({
    queryKey: ["prompt-templates-infinite", search],
    queryFn: ({ pageParam, signal }) =>
      axios.get(endpoints.develop.runPrompt.createTemplateId, {
        params: {
          modality: "chat",
          ...(search && { name: search }),
          page_size: 10,
          page: pageParam,
        },
        signal,
      }),
    getNextPageParam: (lastPage) => {
      const data = lastPage.data;
      if (data?.current_page < data?.total_pages) return data.current_page + 1;
      return undefined;
    },
    initialPageParam: 1,
    staleTime: 30 * 1000,
    ...options,
  });

const LIBRARY_TEMPLATE_PAGE_SIZE = 10;

/**
 * Infinite-scroll hook for the prompt library/base templates.
 * Used by PromptNodePopper to show reusable library templates alongside
 * user-saved prompt templates.
 * @param {string} search - Search query to filter by name
 * @param {object} options - Additional react-query options
 */
export const useGetLibraryTemplatesInfinite = (search, options = {}) =>
  useInfiniteQuery({
    queryKey: ["library-templates-infinite", search],
    queryFn: ({ pageParam, signal }) =>
      axios.get(endpoints.develop.runPrompt.promptTemplate, {
        params: {
          ...(search && { name: search }),
          // The live base-template view currently reads zero-indexed
          // page_number/page_size, while generated contracts expose
          // one-indexed page/limit. Send both coherent pairs so this
          // selector stays compatible during that contract convergence.
          page: pageParam + 1,
          limit: LIBRARY_TEMPLATE_PAGE_SIZE,
          page_size: LIBRARY_TEMPLATE_PAGE_SIZE,
          page_number: pageParam,
        },
        signal,
      }),
    getNextPageParam: getNextLibraryTemplatePageParam,
    initialPageParam: 0,
    staleTime: 30 * 1000,
    ...options,
  });

/**
 * Infinite-scroll variant of useGetPromptVersions.
 * Used by PromptNameRow version dropdown (paginated, 10 per page).
 * @param {string} templateId - The prompt template ID
 * @param {object} options - Additional react-query options
 */
export const useGetPromptVersionsInfinite = (templateId, options = {}) =>
  useInfiniteQuery({
    queryKey: ["prompt-versions-infinite", templateId],
    queryFn: ({ pageParam, signal }) =>
      axios.get(endpoints.develop.runPrompt.getPromptVersions(), {
        params: {
          template_id: templateId,
          page: pageParam,
        },
        signal,
      }),
    getNextPageParam: (lastPage) => {
      const data = lastPage.data;
      if (data?.current_page < data?.total_pages) return data.current_page + 1;
      return undefined;
    },
    initialPageParam: 1,
    staleTime: 30 * 1000,
    enabled: !!templateId,
    ...options,
  });
