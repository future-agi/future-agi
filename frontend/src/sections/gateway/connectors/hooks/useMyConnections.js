import { useCallback, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

import { authorizeTarget, connectTarget, openSignIn } from "../connect-flow";

// The reader's own accounts on the organization's per-person MCP servers
// (GET /agentcc/mcp/connect/, M25x): one row per server, with whether the
// reader is connected and whether a sign-in can start here. The API answers
// only the reader's own account, never a teammate's.
export const MY_CONNECTIONS_QUERY_KEY = ["gateway-mcp-my-connections"];

// The list route, or null on a console whose API contract does not list it
// yet; without it the panel is not drawn.
export function myConnectionsRoute() {
  const route = endpoints.gateway?.mcpConnections;
  return typeof route === "string" ? route : null;
}

// `enabled` false holds the read (the page sets it while MCP is off).
export function useMyConnections({ enabled = true, k2Apps = false } = {}) {
  const route = myConnectionsRoute();
  return useQuery({
    queryKey: [...MY_CONNECTIONS_QUERY_KEY, k2Apps],
    queryFn: async () => {
      const { data } = await axios.get(route);
      const rows = data?.result?.connections;
      return Array.isArray(rows) ? rows : [];
    },
    staleTime: 30_000,
    enabled: Boolean(route) && enabled,
  });
}

export function useRefreshMyConnections() {
  const queryClient = useQueryClient();
  return useCallback(
    () => queryClient.invalidateQueries({ queryKey: MY_CONNECTIONS_QUERY_KEY }),
    [queryClient],
  );
}

// An error the start ends in before or after the API answered, with the
// API's code when it refused.
export class StartRefused extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

// Starts the sign-in of the reader's own account on one server, only from a
// click: POSTs the row's start_url (checked to lie on the API's connect
// routes) with the browser's credentials, so the API's sign-in cookie is
// kept for the callback, then follows the authorize URL only where
// authorizeTarget accepts it. A binding, from a gateway connect link, goes
// as it came; the API refuses one made for someone else. Not a React Query
// mutation on purpose: a mutation keeps its variables, the binding included,
// in the query client's cache.
export function useStartConnect() {
  const [pending, setPending] = useState(null);
  const start = useCallback(async (connection, binding) => {
    const target = connectTarget(connection?.start_url);
    if (!target) {
      throw new StartRefused(
        "not_startable",
        "A sign-in cannot start for this server here.",
      );
    }
    setPending(connection.server_id);
    try {
      const body = binding ? { binding } : {};
      let data;
      try {
        ({ data } = await axios.post(target, body, { withCredentials: true }));
      } catch (err) {
        throw new StartRefused(
          typeof err?.code === "string" ? err.code : "",
          typeof err?.message === "string" ? err.message : "",
        );
      }
      const next = authorizeTarget(data?.result?.authorize_url);
      if (!next) {
        throw new StartRefused(
          "authorize_url_refused",
          "The server's sign-in page cannot be opened from this console.",
        );
      }
      openSignIn(next);
    } finally {
      setPending(null);
    }
  }, []);
  return { start, pending };
}
