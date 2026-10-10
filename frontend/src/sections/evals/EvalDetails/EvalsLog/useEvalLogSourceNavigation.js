import { useCallback, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";

export const sourceNavigationKey = (orgId, wsId, logId) => [
  "evalLogSourceNavigation",
  orgId,
  wsId,
  logId,
];

export function useEvalLogSourceNavigation(logId, { enabled = true } = {}) {
  const { currentOrganizationId } = useOrganization();
  const { currentWorkspaceId } = useWorkspace();
  const queryClient = useQueryClient();
  const key = useMemo(
    () => sourceNavigationKey(currentOrganizationId, currentWorkspaceId, logId),
    [currentOrganizationId, currentWorkspaceId, logId],
  );
  const queryFn = useCallback(
    async ({ signal }) => {
      const response = await axios.get(endpoints.develop.eval.getEvalLogs, {
        params: { log_id: logId, include_source_navigation: "true" },
        signal,
      });
      return (
        response?.data?.result?.source_navigation ?? {
          status: "unsupported_backend",
          retryable: false,
          kind: null,
          project_id: null,
          trace_id: null,
          span_id: null,
        }
      );
    },
    [logId],
  );
  const options = useMemo(
    () => ({
      queryKey: key,
      queryFn,
      staleTime: 0,
      gcTime: 0,
      retry: false,
      refetchOnWindowFocus: false,
      meta: { errorHandled: true },
    }),
    [key, queryFn],
  );
  const query = useQuery({
    ...options,
    enabled: enabled && !!logId && !!currentOrganizationId,
  });

  // fetchQuery shares an in-flight request and never authorizes from stale data.
  const revalidate = useCallback(
    () => queryClient.fetchQuery(options),
    [queryClient, options],
  );
  return { ...query, revalidate, key };
}
