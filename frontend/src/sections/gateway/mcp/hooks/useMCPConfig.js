import { useQuery, useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

import {
  refreshGatewayConfig,
  useGatewayConfigWrite,
} from "../../providers/hooks/useGatewayConfig";
import { isMCPOffError } from "../utils";

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
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.mcpStatus(gatewayId));
      return data.result;
    },
    enabled: Boolean(gatewayId),
    staleTime: 15000,
    refetchInterval: 30000,
  });
}

export function useMCPTools(gatewayId) {
  return useQuery({
    queryKey: ["agentcc-mcp-tools", gatewayId],
    queryFn: () => readMCPList(endpoints.gateway.mcpTools(gatewayId)),
    enabled: Boolean(gatewayId),
    staleTime: 15000,
    refetchInterval: 30000,
  });
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

export function useMCPResources(gatewayId) {
  return useQuery({
    queryKey: ["agentcc-mcp-resources", gatewayId],
    queryFn: () => readMCPList(endpoints.gateway.mcpResources(gatewayId)),
    enabled: Boolean(gatewayId),
    staleTime: 30000,
    refetchInterval: 60000,
  });
}

export function useMCPPrompts(gatewayId) {
  return useQuery({
    queryKey: ["agentcc-mcp-prompts", gatewayId],
    queryFn: () => readMCPList(endpoints.gateway.mcpPrompts(gatewayId)),
    enabled: Boolean(gatewayId),
    staleTime: 30000,
    refetchInterval: 60000,
  });
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
