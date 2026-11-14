import { useMemo } from "react";
import { useAgentDefinitionVersions } from "src/api/agent-definition/agent-definition-version";
import { toVersion, useAgentVersion } from "src/api/agents/agentDetail";

/**
 * The agent's versions a page at a time, newest first. A selected version
 * that isn't on the first page (a deep link to an old one) is pinned to the
 * top from its own detail, and stays pinned while more pages load; its copy
 * further down is left out so it shows once. The pin follows the first page
 * only, which every view of the agent reads from the same cache, so the menu
 * and the timeline always agree and rows never jump.
 */
export default function usePinnedVersions(agentId, versionId) {
  const list = useAgentDefinitionVersions({ selectedAgentId: agentId });
  const selected = useAgentVersion(agentId, versionId);
  const loaded = useMemo(
    () =>
      (list.data?.pages ?? []).flatMap((p) => p.results ?? []).map(toVersion),
    [list.data],
  );

  const firstPage = list.data?.pages?.[0]?.results;
  const offFirstPage =
    !!versionId && !!firstPage && !firstPage.some((v) => v.id === versionId);
  const pinned = offFirstPage && selected.data ? selected.data : null;
  const inList = loaded.find((v) => v.id === versionId);
  return {
    versions: pinned
      ? [pinned, ...loaded.filter((v) => v.id !== versionId)]
      : loaded,
    selected: inList ?? selected.data ?? null,
    loaded: !!list.data,
    isLoading: list.isLoading,
    isError: list.isError && !list.data,
    error: list.error,
    refetch: list.refetch,
    hasNextPage: list.hasNextPage,
    isFetchingNextPage: list.isFetchingNextPage,
    fetchNextPage: list.fetchNextPage,
  };
}
