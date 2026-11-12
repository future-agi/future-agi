import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";

import { MCP_API } from "../api";
import { cursorFrom } from "../calls/toolCalls";
import { useMCPToolCallFeatures } from "./useMCPToolCallFeatures";

export const TOOL_CALLS_QUERY_KEY = "agentcc-mcp-tool-calls";

// The capability is part of the cache identity. OFF also masks cached data
// and guards manual refetch/fetchNextPage, which bypass React Query's enabled.
export function useMCPToolCalls(params, { enabled = true, gatewayId } = {}) {
  const { explorer } = useMCPToolCallFeatures(gatewayId);
  const allowed = explorer && enabled;
  const query = useInfiniteQuery({
    queryKey: [TOOL_CALLS_QUERY_KEY, gatewayId, params, allowed],
    queryFn: async ({ pageParam, signal }) => {
      if (!allowed) return null;
      const { data } = await axios.get(MCP_API.toolCalls, {
        params: { ...params, cursor: pageParam },
        signal,
      });
      return data;
    },
    initialPageParam: undefined,
    getNextPageParam: (lastPage) => cursorFrom(lastPage?.next),
    enabled: allowed,
    staleTime: 15000,
  });
  return { ...query, data: allowed ? query.data : undefined };
}

// One stored call. Shared trace viewing is deliberately not gated here.
export function useMCPToolCall(id, gatewayId) {
  const { explorer } = useMCPToolCallFeatures(gatewayId);
  const allowed = explorer && id !== null && id !== undefined;
  const query = useQuery({
    queryKey: [TOOL_CALLS_QUERY_KEY, gatewayId, "detail", id, allowed],
    queryFn: async ({ signal }) => {
      if (!allowed) return null;
      const { data } = await axios.get(MCP_API.toolCall(id), { signal });
      return data.result;
    },
    enabled: allowed,
    staleTime: 60000,
  });
  return { ...query, data: allowed ? query.data : undefined };
}
