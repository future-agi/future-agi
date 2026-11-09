import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { useOrganization } from "src/contexts/OrganizationContext";
import { authorizeTarget, openSignIn } from "../connect-flow";
import { CATALOG_QUERY_KEY } from "./useConnectorCatalog";
import { MY_CONNECTIONS_QUERY_KEY } from "./useMyConnections";

const route = (appId) =>
  apiPath("/agentcc/connectors/apps/{app_id}/oauth/", { app_id: appId });

// Select only value-free configuration metadata before anything enters a cache.
function configuration(result) {
  return {
    configured: result.configured === true,
    has_secret: result.has_secret === true,
    mode: result.mode,
    registration: result.registration,
    discovery_url: result.discovery_url,
    redirect_uri: result.redirect_uri,
    gateway_synced: result.gateway_synced,
  };
}

export function useMCPOAuthApp(appId) {
  const { currentOrganizationId } = useOrganization();
  const queryClient = useQueryClient();
  const queryKey = ["gateway-mcp-oauth-app", currentOrganizationId, appId];
  const [pending, setPending] = useState(false);
  const query = useQuery({
    queryKey,
    queryFn: async () =>
      configuration((await axios.get(route(appId))).data.result),
    staleTime: 0,
  });
  // Never use a mutation cache: manual secrets live only in the request.
  const request = async (body, discovery = false) => {
    setPending(true);
    try {
      const target = discovery
        ? apiPath("/agentcc/connectors/apps/{app_id}/oauth/discovery/", {
            app_id: appId,
          })
        : route(appId);
      const { data } = await axios.post(target, body, {});
      if (discovery)
        return { dcr_supported: data.result.dcr_supported === true };
      const result = configuration(data.result);
      queryClient.setQueryData(queryKey, result);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: CATALOG_QUERY_KEY }),
        queryClient.invalidateQueries({ queryKey: MY_CONNECTIONS_QUERY_KEY }),
      ]);
      return result;
    } finally {
      setPending(false);
    }
  };
  const signIn = async (mode) => {
    setPending(true);
    try {
      const { data } = await axios.post(
        apiPath("/agentcc/mcp/connect/{server_id}/start/", {
          server_id: appId,
        }),
        { mode: mode === "shared" ? "shared" : "user" },
        { withCredentials: true },
      );
      const next = authorizeTarget(data?.result?.authorize_url);
      if (!next) throw new Error("Sign-in unavailable");
      openSignIn(next);
    } finally {
      setPending(false);
    }
  };
  return { ...query, request, signIn, pending };
}
