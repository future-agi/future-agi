import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import {
  isCachedMCPRuntimeOff,
  useMCPRuntimeOff,
} from "../hooks/useMCPRuntimeOff";

// Main owns this registry and its generated contract. Never fall back to a
// handwritten URL or call axios with undefined (which would hit the base URL).
export function approvalRoutesReady() {
  return (
    typeof endpoints.gateway.mcpApprovals === "string" &&
    typeof endpoints.gateway.mcpApproval === "function" &&
    typeof endpoints.gateway.mcpApprovalDecision === "function"
  );
}

export function useApprovals(gatewayId, enabled) {
  const cache = useQueryClient();
  const runtimeOff = useMCPRuntimeOff(gatewayId);
  return useQuery({
    queryKey: ["mcp-approvals", gatewayId],
    queryFn: async () => {
      // Manual refetch also respects the existing retained runtime restriction.
      if (!gatewayId || !enabled || isCachedMCPRuntimeOff(cache, gatewayId))
        return [];
      if (!approvalRoutesReady())
        throw new Error("Approval routes are not installed.");
      const { data } = await axios.get(endpoints.gateway.mcpApprovals);
      if (!Array.isArray(data?.result))
        throw new Error("Approval state could not be read.");
      return data.result;
    },
    enabled:
      Boolean(gatewayId) &&
      enabled === true &&
      !runtimeOff &&
      approvalRoutesReady(),
    refetchInterval: 5000,
    staleTime: 0,
  });
}

export function useApprovalDecision(gatewayId) {
  const cache = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: async ({ approvalId, decision }) => {
      if (!gatewayId || isCachedMCPRuntimeOff(cache, gatewayId))
        throw new Error("MCP is unavailable on this gateway.");
      if (!approvalRoutesReady())
        throw new Error("Approval routes are not installed.");
      const { data } = await axios.post(
        endpoints.gateway.mcpApprovalDecision(approvalId),
        { decision },
      );
      return data.result;
    },
    onSettled: () =>
      cache.invalidateQueries({ queryKey: ["mcp-approvals", gatewayId] }),
  });
}
