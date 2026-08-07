import { useMemo } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const workspacesListKey = ["workspaces-list"];

const WORKSPACES_PAGE_LIMIT = 100;

const flattenPages = (data) =>
  data.pages.flatMap((page) => page?.data?.results || []);

export function useWorkspacesList({ enabled = true } = {}) {
  return useInfiniteQuery({
    queryKey: workspacesListKey,
    queryFn: ({ pageParam }) =>
      axios.get(endpoints.workspaces.list, {
        params: { page: pageParam, limit: WORKSPACES_PAGE_LIMIT },
      }),
    getNextPageParam: ({ data }) =>
      data?.next ? data?.current_page + 1 : null,
    initialPageParam: 1,
    staleTime: Infinity,
    select: flattenPages,
    enabled,
  });
}

export function useWorkspaceFromList(workspaceId, { enabled = true } = {}) {
  const query = useWorkspacesList({ enabled: enabled && !!workspaceId });

  const workspace = useMemo(
    () => (query.data || []).find((ws) => ws.id === workspaceId) || null,
    [query.data, workspaceId],
  );

  return { ...query, workspace };
}
