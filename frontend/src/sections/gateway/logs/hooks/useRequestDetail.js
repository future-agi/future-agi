import { useQuery } from "@tanstack/react-query";
import axiosInstance, { endpoints } from "src/utils/axios";
import { normalizeGatewayMetadataResponse } from "../../utils/metadataDisplay";

/**
 * Fetches the full detail for a single request log entry.
 *
 * @param {string|null|undefined} logId - The ID of the request log to fetch.
 * @returns {{ data: Object|undefined, isLoading: boolean, error: Error|null }}
 */
export default function useRequestDetail(logId) {
  return useQuery({
    queryKey: ["requestLogDetail", logId],
    queryFn: async () => {
      const res = await axiosInstance.get(
        endpoints.gateway.requestLogDetail(logId),
      );
      return normalizeGatewayMetadataResponse(res.data);
    },
    enabled: Boolean(logId),
  });
}
