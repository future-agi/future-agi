import { useCallback, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { isMCPRuntimeOff } from "./mcpRuntimeState";

// Read the cached restriction, including from a manual refetch's queryFn.
// Both rejected reads and successful-but-degraded status envelopes retain it.
// Without a prior authoritative OFF, an error never establishes runtime off.
export function isCachedMCPRuntimeOff(client, gatewayId) {
  const state = client.getQueryState(["agentcc-mcp-status", gatewayId]);
  return isMCPRuntimeOff(state?.data);
}

// Subscribe without issuing another status request or changing its polling.
// This covers all live-list callers, including ToolCallsTab/VirtualServersTab.
export function useMCPRuntimeOff(gatewayId) {
  const client = useQueryClient();
  const subscribe = useCallback(
    (notify) =>
      client.getQueryCache().subscribe((event) => {
        const key = event.query?.queryKey;
        if (key?.[0] === "agentcc-mcp-status" && key[1] === gatewayId) notify();
      }),
    [client, gatewayId],
  );
  const snapshot = useCallback(
    () => isCachedMCPRuntimeOff(client, gatewayId),
    [client, gatewayId],
  );
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
