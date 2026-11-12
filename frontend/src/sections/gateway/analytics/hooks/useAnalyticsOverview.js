import { useQuery, keepPreviousData } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

// The overview reads start, end and api_key_id (agentcc/views/analytics.py);
// its contract has no gateway_id, so none is sent: a dev build enforces the
// request contract and refuses an unknown key. `gatewayId` stays in the
// signature for the callers that pass it.
export function useAnalyticsOverview(
  // eslint-disable-next-line no-unused-vars
  { start, end, gatewayId, apiKeyId } = {},
  options = {},
) {
  const params = {};
  if (start) params.start = start;
  if (end) params.end = end;
  if (apiKeyId) params.api_key_id = apiKeyId;

  return useQuery({
    queryKey: ["analytics", "overview", params],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.gateway.analyticsOverview, {
        params,
      });
      return data.result;
    },
    placeholderData: keepPreviousData,
    staleTime: 60000,
    refetchOnWindowFocus: false,
    ...options,
  });
}
