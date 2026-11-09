import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";

import { refreshGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import { MCP_API } from "../api";

export const CREDENTIALS_QUERY_KEY = "agentcc-mcp-credentials";

// The org's MCP credential vault (GET /agentcc/mcp/credentials/): every
// credential's metadata, never its value. One page of up to 200.
export function useMCPCredentials() {
  return useQuery({
    queryKey: [CREDENTIALS_QUERY_KEY],
    queryFn: async () => {
      const { data } = await axios.get(MCP_API.credentials, {
        params: { limit: 200 },
      });
      return { rows: data?.results || [], count: data?.count ?? 0 };
    },
    staleTime: 15000,
  });
}

// A vault write changes the server's credential reference in the org config
// (create, revoke) or pushes the config again (rotate), so both are re-read.
function useVaultWrite(mutationFn) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [CREDENTIALS_QUERY_KEY] });
      return refreshGatewayConfig(queryClient, ["agentcc-mcp-status"]);
    },
  });
}

export function useCreateMCPCredential() {
  return useVaultWrite(async (body) => {
    const { data } = await axios.post(MCP_API.credentials, body);
    return data.result;
  });
}

export function useRotateMCPCredential() {
  return useVaultWrite(async ({ id, body }) => {
    const { data } = await axios.post(MCP_API.rotateCredential(id), body);
    return data.result;
  });
}

export function useRevokeMCPCredential() {
  return useVaultWrite(async (id) => {
    const { data } = await axios.delete(MCP_API.credential(id));
    return data.result;
  });
}
