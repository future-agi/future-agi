import { useInfiniteQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import axios, { endpoints } from "src/utils/axios";
import {
  ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
  getNextAttributeKeyPageParam,
  readAttributeKeyPage,
} from "src/sections/projects/LLMTracing/attributeKeyCursorPagination";

export const useRunInsightAttributeKeys = (projectId) => {
  const query = useInfiniteQuery({
    queryKey: ["run-insights-span-attribute-keys", projectId],
    queryFn: ({ signal, pageParam }) =>
      readAttributeKeyPage({
        pageParam,
        signal,
        requestPage: (cursor) =>
          axios
            .get(endpoints.project.spanAttributeKeys(), {
              signal,
              timeout: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
              params: {
                project_id: projectId,
                page_size: 50,
                ...(cursor ? { cursor } : {}),
              },
            })
            .then(({ data }) => data || {}),
      }),
    initialPageParam: null,
    getNextPageParam: getNextAttributeKeyPageParam,
    enabled: Boolean(projectId),
    retry: false,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    meta: { errorHandled: true },
  });
  const attributeKeys = useMemo(() => {
    const seenKeys = new Set();
    return (query.data?.pages || []).flatMap((page) =>
      (Array.isArray(page?.result) ? page.result : []).filter(({ key }) => {
        if (!key || seenKeys.has(key)) return false;
        seenKeys.add(key);
        return true;
      }),
    );
  }, [query.data?.pages]);

  return { ...query, attributeKeys };
};
