import { useEffect, useMemo } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useOrganization } from "src/contexts/OrganizationContext";

export const workspacesListKey = ["workspaces-list"];

const WORKSPACES_PAGE_LIMIT = 100;

const flattenPages = (data) =>
  data.pages.flatMap((page) => page?.data?.results || []);

export function useWorkspacesList({ enabled = true, workspaceId } = {}) {
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

  // Org resolution can finish without producing an id: seedFromMembership sets
  // isReady in its catch and on an empty membership while leaving the id null.
  // `enabled` then keeps the query off for good, so it can never report success
  // or failure on its own — every state below has to come from the org context.
  const orgResolving = enabled && !orgReady && !currentOrganizationId;
  const orgUnavailable = enabled && orgReady && !currentOrganizationId;

  const data = query.data;
  const fetchNextPage = query.fetchNextPage;
  // A settings lookup must search beyond the switcher's loaded pages before
  // treating a workspace as absent. List-only consumers remain scroll-driven.
  const needsMorePages =
    enabled &&
    !!workspaceId &&
    !!currentOrganizationId &&
    !query.isError &&
    !!query.hasNextPage &&
    !data?.some((workspace) => workspace.id === workspaceId);
  // Only a missing-workspace lookup tracks background fetching; ordinary list
  // consumers retain their narrowed query subscription.
  const isFetchingForLookup = needsMorePages && query.isFetching;

  useEffect(() => {
    if (needsMorePages && !isFetchingForLookup) {
      // Multiple guards/pages can share this query. Do not cancel their fetch
      // (or an in-flight refresh) by starting the same next page again.
      fetchNextPage({ cancelRefetch: false });
    }
    // A new page must advance the search even if fetch transitions were batched.
  }, [needsMorePages, isFetchingForLookup, fetchNextPage, data]);

  // Spreading the result would read every property, marking them all tracked
  // and re-rendering consumers on transitions none of them use. Adding a
  // property here is the price of a consumer needing one.
  return {
    data,
    fetchNextPage,
    // The role guard's Retry. The observer binds refetch once, so reading it
    // tracks a value that never changes and adds no render.
    refetch: query.refetch,
    // A disabled query is still pending, which is what the switcher renders on
    // — it would skeleton forever once the org is known to be unavailable.
    isPending: query.isPending && !orgUnavailable,
    isFetchingNextPage: query.isFetchingNextPage,
    // Scope could not be established, which is terminal: without this the role
    // guard has no exit, since its only one is isError.
    isError: query.isError || orgUnavailable,
    // Which isError is terminal: no refetch can help while the org is unknown
    // (the query stays disabled), so callers must not offer a Retry for it.
    isOrgUnavailable: orgUnavailable,
    // A disabled query is not "loading", but callers have nothing to render
    // while the org is still being resolved.
    isLoading: query.isLoading || orgResolving || needsMorePages,
  };
}

export function useWorkspaceFromList(workspaceId, { enabled = true } = {}) {
  const query = useWorkspacesList({
    enabled: enabled && !!workspaceId,
    workspaceId,
  });

  const workspace = useMemo(
    () => (query.data || []).find((ws) => ws.id === workspaceId) || null,
    [query.data, workspaceId],
  );

  // `query` is the narrowed object above, not the tracked proxy, so spreading
  // it here reads only plain properties.
  return { ...query, workspace };
}
