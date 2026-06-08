import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import axios, { endpoints } from "src/utils/axios";
import {
  buildAgentBuilderHref,
  buildAgentCreatedPayload,
} from "src/sections/agent-playground/agentOnboardingEvents";

// Shared mapping from API response → currentAgent store shape.
// Uses snake_case to match the store fields written by updateVersion(),
// so consumers like NodeDrawer / GraphView / useSaveDraft can read a single
// consistent shape regardless of which code path hydrated currentAgent.
export const mapGraphToAgent = (result) => ({
  id: result?.id,
  version_id: result?.active_version?.id,
  version_number: result?.active_version?.version_number,
  version_name: result?.active_version?.version_number
    ? `Version ${result.active_version.version_number}`
    : "",
  name: result?.name,
  description: result?.description,
  created_at: result?.created_at,
  updated_at: result?.updated_at,
  is_draft: result?.active_version?.status === "draft",
  version_status: result?.active_version?.status,
});

/**
 * Hook for creating a new graph and navigating to the builder.
 * The graph is named with the current datetime so it's always unique.
 *
 * @param {Object} options
 * @param {Function} options.navigate        - useNavigate() from react-router
 * @param {Function} options.setCurrentAgent - store setter for the active agent
 * @param {Function} [options.onSuccess]     - optional callback after navigation
 */
export const useCreateGraph = ({
  navigate,
  onboardingMode,
  quickStartAttribution,
  recordActivationEvent,
  setCurrentAgent,
  onSuccess,
} = {}) =>
  useMutation({
    mutationFn: () => {
      const now = new Date();
      return axios.post(endpoints.agentPlayground.createGraph, {
        name: `Agent ${format(now, "MMM dd, yyyy h:mm a")}`,
        description: `Created on ${format(now, "MMM dd, yyyy 'at' h:mm a")}`,
      });
    },
    onSuccess: (res) => {
      const result = res.data?.result;
      if (!result) return;
      const agent = mapGraphToAgent(result);
      const searchParams = new URLSearchParams();
      if (result.active_version?.id) {
        searchParams.set("version", result.active_version.id);
      }
      if (onboardingMode) {
        searchParams.set("onboarding", onboardingMode);
      }
      const search = searchParams.toString();
      setCurrentAgent?.(agent);
      if (onboardingMode) {
        recordActivationEvent?.(
          buildAgentCreatedPayload({
            agentId: result.id,
            quickStartAttribution,
          }),
        );
      }
      navigate?.(
        onboardingMode
          ? buildAgentBuilderHref({
              agentId: result.id,
              journeyStep:
                onboardingMode === "run-scenario"
                  ? "add_agent_node"
                  : undefined,
              onboarding: onboardingMode,
              quickStartAttribution,
              tourAnchor:
                onboardingMode === "run-scenario"
                  ? "agent_add_node_button"
                  : undefined,
              versionId: result.active_version?.id,
            })
          : `/dashboard/agents/playground/${result.id}/build${search ? `?${search}` : ""}`,
      );
      onSuccess?.(agent);
    },
  });

/**
 * Hook for fetching the list of agent playground graphs
 * @param {object} options - Additional react-query options
 */
export const useGetGraphsList = (options = {}) =>
  useQuery({
    queryKey: ["agent-playground", "graphs"],
    queryFn: () => axios.get(endpoints.agentPlayground.listGraphs),
    select: (res) =>
      (res.data?.result?.graphs ?? []).map((graph) => ({
        id: graph.id,
        name: graph.name,
        description: graph.description,
        noOfNodes: graph.node_count || 0,
        createdBy: graph.created_by?.name ?? "",
        collaborators: [],
        created: graph.created_at,
        updated: graph.updated_at,
      })),
    staleTime: 30 * 1000,
    ...options,
  });

/**
 * Hook for fetching a single graph's detail and mapping it to the agent store shape.
 * Used by AgentPlaygroundView when navigating from the list (no currentAgent in store).
 * @param {string} graphId
 * @param {object} options - Additional react-query options
 */
export const useGetGraphDetail = (graphId, options = {}) =>
  useQuery({
    queryKey: ["agent-playground", "graph", graphId],
    queryFn: () => axios.get(endpoints.agentPlayground.graphDetail(graphId)),
    select: (res) => mapGraphToAgent(res.data?.result),
    staleTime: 30 * 1000,
    enabled: !!graphId,
    ...options,
  });

/**
 * Hook for updating graph metadata (name, description).
 * Caller is responsible for optimistic store updates and rollback on error.
 * @param {object} options - useMutation options
 */
export const useUpdateGraph = (options = {}) => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, ...data }) =>
      axios.patch(endpoints.agentPlayground.updateGraph(id), data),
    onSuccess: (res, variables, context) => {
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graph", variables.id],
      });
      options.onSuccess?.(res, variables, context);
    },
    ...options,
  });
};

/**
 * Hook for bulk deleting agent playground graphs
 * @param {object} options - useMutation options (onSuccess, onError, etc.)
 */
export const useDeleteGraphs = (options = {}) =>
  useMutation({
    mutationFn: ({ selectAll, ids, excludeIds }) =>
      axios.post(endpoints.agentPlayground.deleteGraphs, {
        select_all: selectAll,
        ...(selectAll ? { exclude_ids: excludeIds } : { ids }),
      }),
    onSuccess: (...args) => options.onSuccess?.(...args),
    ...options,
  });
