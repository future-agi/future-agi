import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

import { credentialRequestBody } from "../connect-request";

// The connector catalog API (CN9) and the MCP credential vault (M14). The
// page reads every app the org can add or has added in one list and filters
// it in the browser, so search, categories and the Connected view answer at
// once and agree with each other.
export const CATALOG_QUERY_KEY = ["gateway-connector-catalog"];

const PAGE_SIZE = 200; // the API's largest page
const MAX_PAGES = 50;

async function fetchCatalog() {
  const apps = [];
  let catalog = null;
  for (let page = 1; page <= MAX_PAGES; page += 1) {
    const { data } = await axios.get(endpoints.gateway.connectorCatalog, {
      params: { limit: PAGE_SIZE, page },
    });
    apps.push(...(data.results || []));
    catalog = data.catalog || catalog;
    if (!data.next || page >= (data.total_pages || 1)) break;
  }
  return { apps, catalog };
}

// `enabled` false holds the read (the page sets it while the deployment
// runs with MCP off, when there is no app to connect).
export function useConnectorCatalog({
  enabled = true,
  upstreamOAuth = false,
  k2Apps = false,
} = {}) {
  return useQuery({
    // A capability change must not reuse a catalogue fetched while a feature was ON.
    queryKey: [...CATALOG_QUERY_KEY, upstreamOAuth, k2Apps],
    queryFn: fetchCatalog,
    staleTime: 30_000,
    enabled,
  });
}

function useRefreshCatalog() {
  const queryClient = useQueryClient();
  return useCallback(
    () => queryClient.invalidateQueries({ queryKey: CATALOG_QUERY_KEY }),
    [queryClient],
  );
}

// POST apps/{app}/add/ with an addRequestBody body, which carries the typed
// key (CN-ADD); the result names the server and says its key is stored. Not
// a React Query mutation on purpose, as useStoreConnectorKey: a mutation
// keeps its variables (the key) and its error (a refusal may repeat the key)
// in the query client's cache; this keeps them only for the request.
export function useAddConnector() {
  const refresh = useRefreshCatalog();
  const [isPending, setPending] = useState(false);
  const add = useCallback(
    async (appId, body) => {
      setPending(true);
      try {
        const { data } = await axios.post(
          endpoints.gateway.connectorAdd(appId),
          body,
        );
        return data.result;
      } finally {
        setPending(false);
        refresh();
      }
    },
    [refresh],
  );
  return { add, isPending };
}

// DELETE apps/{app}/add/: removes the app's servers and virtual servers and
// revokes their stored keys.
export function useRemoveConnector() {
  const refresh = useRefreshCatalog();
  return useMutation({
    mutationFn: /** @param {{ appId: string }} variables */ async ({
      appId,
    }) => {
      const { data } = await axios.delete(
        endpoints.gateway.connectorAdd(appId),
      );
      return data.result;
    },
    onSettled: refresh,
  });
}

// Stores a shared key in the vault. Not a React Query mutation on purpose:
// a mutation keeps its variables, the key included, in the query client's
// cache; this keeps the key only for the length of the request.
export function useStoreConnectorKey() {
  const refresh = useRefreshCatalog();
  const [isPending, setPending] = useState(false);
  const store = useCallback(
    async (serverId, credential, value) => {
      setPending(true);
      try {
        const { data } = await axios.post(
          endpoints.gateway.mcpCredentials,
          credentialRequestBody(serverId, credential, value),
        );
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

// DELETE a sign-in account at the URL the API named for it, which the page
// has checked lies on the API's own sign-in path (connect-flow.js
// connectTarget): revokes the gateway's access to that account.
export function useDisconnectAccount() {
  const refresh = useRefreshCatalog();
  return useMutation({
    mutationFn: async ({ url }) => {
      const { data } = await axios.delete(url);
      return data?.result;
    },
    onSettled: refresh,
  });
}
