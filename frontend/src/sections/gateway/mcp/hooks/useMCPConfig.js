import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { isCachedMCPRuntimeOff, useMCPRuntimeOff } from "./useMCPRuntimeOff";
import axios, { endpoints } from "src/utils/axios";

import {
  refreshGatewayConfig,
  useGatewayConfigWrite,
} from "../../providers/hooks/useGatewayConfig";
import { isMCPOffError } from "../utils";
import { readMCPStatus } from "./readMCPStatus";
import { retainMCPRuntimeRestriction } from "./mcpRuntimeState";

// An MCP list read. With MCP off for the deployment the list is empty: that is
// a state the status reports, so the read raises no error (and no toast). Any
// other failure, a gateway that is down included, stays an error.
async function readMCPList(url) {
  try {
    const { data } = await axios.get(url);
    return data.result;
  } catch (error) {
    if (isMCPOffError(error)) return [];
    throw error;
  }
}

export function useMCPStatus(gatewayId) {
  return useQuery({
    queryKey: ["agentcc-mcp-status", gatewayId],
    queryFn: ({ signal }) => readMCPStatus(gatewayId, signal),
    structuralSharing: retainMCPRuntimeRestriction,
    enabled: Boolean(gatewayId),
    // Keep the initial mount deadline bounded; polling/manual refresh can retry.
    retry: false,
    retryOnMount: false,
    staleTime: 15000,
    refetchInterval: 30000,
  });
}

function useMCPList(
  gatewayId,
  kind,
  endpoint,
  interval,
  { enabled = true } = {},
) {
  const client = useQueryClient();
  const runtimeOff = useMCPRuntimeOff(gatewayId);
  const queryKey = [`agentcc-mcp-${kind}`, gatewayId];
  return useQuery({
    queryKey,
    // enabled=false does not guard manual refetch. Check the latest status too.
    queryFn: () =>
      !gatewayId || !enabled || isCachedMCPRuntimeOff(client, gatewayId)
        ? client.getQueryData(queryKey) || []
        : readMCPList(endpoint(gatewayId)),
    enabled: Boolean(gatewayId) && enabled && !runtimeOff,
    staleTime: interval / 2,
    refetchInterval: interval,
  });
}

export function useMCPTools(gatewayId, options) {
  return useMCPList(
    gatewayId,
    "tools",
    endpoints.gateway.mcpTools,
    30000,
    options,
  );
}

// MCP config writes go through useGatewayConfigWrite: a save the gateway did
// not apply (gateway_synced:false) is reported as such, never as a success.
const refreshMCPServers = (queryClient) =>
  refreshGatewayConfig(
    queryClient,
    ["agentcc-mcp-status"],
    ["agentcc-mcp-tools"],
  );

async function postForResult(url, body) {
  const { data } = await axios.post(url, body);
  return data.result;
}

export function useUpdateMCPServer() {
  return useGatewayConfigWrite(
    ({ gatewayId, serverId, config }) =>
      postForResult(endpoints.gateway.updateMcpServer(gatewayId), {
        server_id: serverId,
        config,
      }),
    refreshMCPServers,
  );
}

export function useRemoveMCPServer() {
  return useGatewayConfigWrite(
    ({ gatewayId, serverId }) =>
      postForResult(endpoints.gateway.removeMcpServer(gatewayId), {
        server_id: serverId,
      }),
    refreshMCPServers,
  );
}

export function useUpdateMCPGuardrails() {
  return useGatewayConfigWrite(
    ({ gatewayId, config }) =>
      postForResult(endpoints.gateway.updateMcpGuardrails(gatewayId), {
        config,
      }),
    (queryClient) => refreshGatewayConfig(queryClient, ["agentcc-mcp-status"]),
  );
}

export function useMCPResources(gatewayId, options) {
  return useMCPList(
    gatewayId,
    "resources",
    endpoints.gateway.mcpResources,
    60000,
    options,
  );
}

export function useMCPPrompts(gatewayId, options) {
  return useMCPList(
    gatewayId,
    "prompts",
    endpoints.gateway.mcpPrompts,
    60000,
    options,
  );
}

export function useTestMCPTool() {
  return useMutation({
    mutationFn: async ({ gatewayId, name, arguments: args }) => {
      const { data } = await axios.post(
        endpoints.gateway.testMcpTool(gatewayId),
        { name, arguments: args || {} },
      );
      return data.result;
    },
  });
}
