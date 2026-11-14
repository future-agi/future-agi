import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

import { refreshGatewayConfig } from "../../providers/hooks/useGatewayConfig";

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
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.mcpTools(gatewayId));
      return data.result;
    },
    enabled: Boolean(gatewayId),
    staleTime: 15000,
    refetchInterval: 30000,
  });
}

export function useUpdateMCPServer() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ gatewayId, serverId, config }) => {
      const { data } = await axios.post(
        endpoints.gateway.updateMcpServer(gatewayId),
        { server_id: serverId, config },
      );
      return data.result;
    },
    onSuccess: () =>
      refreshGatewayConfig(
        queryClient,
        ["agentcc-mcp-status"],
        ["agentcc-mcp-tools"],
      ),
  });
}

export function useRemoveMCPServer() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ gatewayId, serverId }) => {
      const { data } = await axios.post(
        endpoints.gateway.removeMcpServer(gatewayId),
        { server_id: serverId },
      );
      return data.result;
    },
    onSuccess: () =>
      refreshGatewayConfig(
        queryClient,
        ["agentcc-mcp-status"],
        ["agentcc-mcp-tools"],
      ),
  });
}

export function useUpdateMCPGuardrails() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ gatewayId, config }) => {
      const { data } = await axios.post(
        endpoints.gateway.updateMcpGuardrails(gatewayId),
        { config },
      );
      return data.result;
    },
    onSuccess: () => refreshGatewayConfig(queryClient, ["agentcc-mcp-status"]),
  });
}

export function useMCPResources(gatewayId) {
  return useQuery({
    queryKey: ["agentcc-mcp-resources", gatewayId],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.gateway.mcpResources(gatewayId),
      );
      return data.result;
    },
    enabled: Boolean(gatewayId),
    staleTime: 30000,
    refetchInterval: 60000,
  });
}

export function useMCPPrompts(gatewayId) {
  return useQuery({
    queryKey: ["agentcc-mcp-prompts", gatewayId],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.mcpPrompts(gatewayId));
      return data.result;
    },
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
