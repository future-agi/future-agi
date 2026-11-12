import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { HOST_API } from "src/config-global";
import { CATALOG_QUERY_KEY } from "./useConnectorCatalog";
import { MY_CONNECTIONS_QUERY_KEY } from "./useMyConnections";

// The org's own OAuth clients for sign-in apps (CN11,
// /agentcc/connectors/oauth-clients/), which only an Owner or Admin may read
// or write. The console offers them only where it has both routes; without
// them nothing of the org's own app is drawn.
export const OAUTH_CLIENTS_QUERY_KEY = ["gateway-connector-oauth-clients"];

const PAGE_SIZE = 200;
const MAX_PAGES = 10;
const CLIENT_GROUP = /^[a-z][a-z0-9-]{1,39}$/;

export function ownAppRoutes() {
  const list = endpoints.gateway?.connectorOAuthClients;
  const detail = endpoints.gateway?.connectorOAuthClient;
  return typeof list === "string" && typeof detail === "function"
    ? { list, detail }
    : null;
}

// The provider card's offer of the org's own OAuth app (connect.own_app,
// connect-flow.js), or null when it makes none the page can use.
export function ownAppOffer(provider, apiBase = HOST_API) {
  const offer = provider?.connect?.own_app;
  if (!offer || typeof offer !== "object") return null;
  if (typeof offer.client_group !== "string") return null;
  if (!CLIENT_GROUP.test(offer.client_group)) return null;
  try {
    const target = new URL(offer.redirect_uri);
    if (target.username || target.password) return null;
    if (target.protocol !== "https:") {
      // An insecure API alone is not permission to advertise insecure vendor
      // callbacks. Only the explicit same-origin local/E2E loopback is allowed.
      const api = new URL(apiBase);
      if (
        target.protocol !== "http:" ||
        target.origin !== api.origin ||
        !["localhost", "127.0.0.1", "[::1]"].includes(api.hostname)
      ) {
        return null;
      }
    }
  } catch {
    return null;
  }
  return {
    clientGroup: offer.client_group,
    redirectUri: offer.redirect_uri,
    required: offer.required === true,
  };
}

async function fetchClients(list) {
  const clients = [];
  for (let page = 1; page <= MAX_PAGES; page += 1) {
    const { data } = await axios.get(list, {
      params: { limit: PAGE_SIZE, page },
    });
    clients.push(...(data.results || []));
    if (!data.next || page >= (data.total_pages || 1)) break;
  }
  return clients;
}

// The org's own app for a sign-in provider, as the drawer shows it: the
// offer, and for an Owner or Admin the client stored for its group. Read
// only when the provider offers it, the reader may manage the org, and the
// console has the routes.
export function useOwnOAuthApp(provider, canManage) {
  const offer = ownAppOffer(provider);
  const routes = ownAppRoutes();
  const enabled = Boolean(offer && canManage && routes);
  const { data, isPending } = useQuery({
    queryKey: OAUTH_CLIENTS_QUERY_KEY,
    queryFn: () => fetchClients(routes.list),
    staleTime: 30_000,
    enabled,
  });
  const current = enabled
    ? (data || []).find(
        (client) =>
          client.client_group === offer.clientGroup &&
          client.status === "active",
      ) || null
    : null;
  return {
    offer: enabled ? offer : null,
    routes,
    current,
    loading: enabled && isPending,
  };
}

function useRefreshClients() {
  const queryClient = useQueryClient();
  return useCallback(
    () =>
      Promise.all(
        [
          OAUTH_CLIENTS_QUERY_KEY,
          CATALOG_QUERY_KEY,
          MY_CONNECTIONS_QUERY_KEY,
        ].map((queryKey) => queryClient.invalidateQueries({ queryKey })),
      ),
    [queryClient],
  );
}

// POST the org's client for a group. Not a React Query mutation on purpose,
// as useStoreConnectorKey: a mutation keeps its variables (the secret) and
// its error (a refusal may repeat it) in the query client's cache; this
// keeps them only for the request.
export function useStoreOwnOAuthClient() {
  const refresh = useRefreshClients();
  const [isPending, setPending] = useState(false);
  const store = useCallback(
    async (list, clientGroup, clientId, clientSecret) => {
      setPending(true);
      try {
        const { data } = await axios.post(list, {
          client_group: clientGroup,
          client_id: clientId,
          client_secret: clientSecret,
        });
        return data.result;
      } finally {
        setPending(false);
        refresh();
      }
    },
    [refresh],
  );
  return { store, isPending };
}

// DELETE the org's client: CN11 revokes it, and the vault keeps no secret.
export function useRemoveOwnOAuthClient() {
  const refresh = useRefreshClients();
  return useMutation({
    mutationFn: async ({ url }) => {
      const { data } = await axios.delete(url);
      return data?.result;
    },
    onSettled: refresh,
  });
}
