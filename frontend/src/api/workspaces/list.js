import { useMemo } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useOrganization } from "src/contexts/OrganizationContext";

export const workspacesListKey = ["workspaces-list"];

const WORKSPACES_PAGE_LIMIT = 100;

const flattenPages = (data) =>
  data.pages.flatMap((page) => page?.data?.results || []);

export function useWorkspacesList({ enabled = true } = {}) {
  const { currentOrganizationId, isReady: orgReady } = useOrganization();

  const query = useInfiniteQuery({
    // A cached list must never be served to a different org.
    queryKey: [...workspacesListKey, currentOrganizationId],
    queryFn: ({ pageParam }) =>
      axios.get(endpoints.workspaces.list, {
        params: { page: pageParam, limit: WORKSPACES_PAGE_LIMIT },
      }),
    getNextPageParam: ({ data }) =>
      data?.next ? data?.current_page + 1 : null,
    initialPageParam: 1,
    staleTime: Infinity,
    select: flattenPages,
    // Firing before the org is known sends no X-Organization-Id.
    enabled: enabled && !!currentOrganizationId,
  });

  return {
    ...query,
    // A disabled query is not "loading", but callers have nothing to render.
    isLoading: query.isLoading || (enabled && !orgReady),
  };
}

export function useWorkspaceFromList(workspaceId, { enabled = true } = {}) {
  const query = useWorkspacesList({ enabled: enabled && !!workspaceId });

  const workspace = useMemo(
    () => (query.data || []).find((ws) => ws.id === workspaceId) || null,
    [query.data, workspaceId],
  );

  return { ...query, workspace };
}
