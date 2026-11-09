import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

import { refreshGatewayConfig } from "../../providers/hooks/useGatewayConfig";

// Writes part of the org's mcp section through update-config, which applies
// it as an RFC 7386 merge patch under the config writer and the org lock
// (agentcc/views/gateway.py update_config): a key it leaves out keeps its
// stored value, and a null removes one. Resolves to the write's result
// ({ version, gateway_synced, warnings, gateway_warning? }).
export function useMCPConfigPatch(gatewayId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (mcpPatch) => {
      const { data } = await axios.post(
        endpoints.gateway.updateConfig(gatewayId),
        {
          mcp: mcpPatch,
        },
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
