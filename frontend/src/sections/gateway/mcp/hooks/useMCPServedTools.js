import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

// No inventory fallback, no placeholderData from a broader key context, and
// no conversion of a failed read to a misleading empty/success result.
export function useMCPServedTools(
  gatewayId,
  vs = "",
  keyId = "",
  serverId = "",
) {
  return useQuery({
    queryKey: ["agentcc-mcp-served-tools", gatewayId, vs, keyId, serverId],
    queryFn: async ({ signal }) => {
      const { data } = await axios.get(
        endpoints.gateway.mcpServedTools(gatewayId),
        {
          params: { vs, key_id: keyId, server_id: serverId },
          signal,
          timeout: 30000,
        },
      );
      if (!Array.isArray(data.result))
        throw new Error("Invalid served-tool list");
      return data.result;
    },
    enabled: Boolean(gatewayId),
    staleTime: 0,
    refetchInterval: 15000,
    meta: { errorHandled: true },
  });
}
