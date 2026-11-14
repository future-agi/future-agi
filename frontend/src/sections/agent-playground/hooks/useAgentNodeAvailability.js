import { useCallback, useMemo, useRef, useState } from "react";
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
 *
 * Failures are classified (PRD R-05 / AC-05) so denial and context errors are
 * never rendered as empty-state setup advice:
 *   - `forbidden`  403 — no access to eligible agents in this context
 *   - `not_found`  404 — current agent unavailable (no existence details)
 *   - `error`      anything else: network, 5xx, malformed body (401/402 are
 *                  handled first by the existing global axios interceptors)
 */
export const AGENT_NODE_AVAILABILITY = {
  LOADING: "loading",
  ERROR: "error",
  FORBIDDEN: "forbidden",
  NOT_FOUND: "not_found",
  EMPTY: "empty",
  READY: "ready",
};

export const classifyAvailabilityError = (error) => {
  // src/utils/axios rejects a flattened `{ ...body, statusCode, transportCode }`
  // (no `response`), so `statusCode` is the production shape; the nested
  // `response.status` / `status` forms cover raw axios and non-axios errors.
  const status = error?.statusCode ?? error?.response?.status ?? error?.status;
  if (status === 403) return AGENT_NODE_AVAILABILITY.FORBIDDEN;
  if (status === 404) return AGENT_NODE_AVAILABILITY.NOT_FOUND;
  return AGENT_NODE_AVAILABILITY.ERROR;
};

const statusFromResult = ({ error, data }) => {
  if (error) return classifyAvailabilityError(error);
  if (!Array.isArray(data)) return AGENT_NODE_AVAILABILITY.ERROR;
  return data.length
    ? AGENT_NODE_AVAILABILITY.READY
    : AGENT_NODE_AVAILABILITY.EMPTY;
};

export default function useAgentNodeAvailability() {
  const { currentAgent } = useAgentPlaygroundStoreShallow((state) => ({
    currentAgent: state.currentAgent,
  }));

  const {
    data: referenceableGraphs,
    isLoading,
    isError,
    error,
    refetch,
  } = useGetReferenceableGraphs(currentAgent?.id);

  // Only a user-initiated refresh counts as "refreshing" — not background or
  // window-focus refetches of the same query.
  const [isRefreshing, setIsRefreshing] = useState(false);
  const refreshSeq = useRef(0);

  const status = useMemo(() => {
    if (isError) return classifyAvailabilityError(error);
    if (isLoading || referenceableGraphs === undefined)
      return AGENT_NODE_AVAILABILITY.LOADING;
    return statusFromResult({ data: referenceableGraphs });
  }, [isError, error, isLoading, referenceableGraphs]);

  /**
   * Re-query eligible agents for the current context without reloading the
   * builder. Resolves to the fresh availability status. Refreshing never
   * inserts anything (PRD Journey 5 / R-08); callers decide what to do with
   * the result. Overlapping refreshes coalesce on the same in-flight request
   * and only the latest one clears the in-flight flag.
   */
  const refresh = useCallback(async () => {
    const seq = ++refreshSeq.current;
    setIsRefreshing(true);
    try {
      const result = await refetch({ cancelRefetch: false });
      return statusFromResult({ error: result.error, data: result.data });
    } finally {
      if (seq === refreshSeq.current) setIsRefreshing(false);
    }
  }, [refetch]);

  return {
    status,
    isReady: status === AGENT_NODE_AVAILABILITY.READY,
    isRefreshing,
    refresh,
  };
}
