import { useCallback, useMemo } from "react";
import { useGetReferenceableGraphs } from "src/api/agent-playground/agent-playground";
import { useAgentPlaygroundStoreShallow } from "../store";

/**
 * Availability states for the Agent node in the current builder context.
 *
 * The Agent node is always *listed* (TH-4549). Whether a click can insert a
 * node depends on whether this user, in this organization/workspace, has at
 * least one eligible agent to reference. Eligibility is decided entirely by
 * the backend `referenceable-graphs` endpoint (non-draft versions, same
 * tenant/workspace, no self/cycle/template references); the frontend only
 * reports what that endpoint returned for the current context.
 *
 * `empty` therefore means "no eligible agents available to you here", never
 * "the organization has no agents". Nothing about inaccessible agents is
 * inferred or shown.
 */
export const AGENT_NODE_AVAILABILITY = {
  LOADING: "loading",
  ERROR: "error",
  EMPTY: "empty",
  READY: "ready",
};

export default function useAgentNodeAvailability() {
  const { currentAgent } = useAgentPlaygroundStoreShallow((state) => ({
    currentAgent: state.currentAgent,
  }));

  const {
    data: referenceableGraphs,
    isLoading,
    isFetching,
    isError,
    refetch,
  } = useGetReferenceableGraphs(currentAgent?.id);

  const status = useMemo(() => {
    if (isError) return AGENT_NODE_AVAILABILITY.ERROR;
    if (isLoading || referenceableGraphs === undefined)
      return AGENT_NODE_AVAILABILITY.LOADING;
    if (!referenceableGraphs.length) return AGENT_NODE_AVAILABILITY.EMPTY;
    return AGENT_NODE_AVAILABILITY.READY;
  }, [isError, isLoading, referenceableGraphs]);

  /**
   * Re-query eligible agents for the current context without reloading the
   * builder. Resolves to the fresh availability status so callers can resume
   * a pending "add Agent node" action only once choices really exist.
   */
  const refresh = useCallback(async () => {
    const result = await refetch();
    if (result.error) return AGENT_NODE_AVAILABILITY.ERROR;
    const graphs = result.data ?? [];
    return graphs.length
      ? AGENT_NODE_AVAILABILITY.READY
      : AGENT_NODE_AVAILABILITY.EMPTY;
  }, [refetch]);

  return {
    status,
    isReady: status === AGENT_NODE_AVAILABILITY.READY,
    isRefreshing: isFetching,
    refresh,
  };
}
