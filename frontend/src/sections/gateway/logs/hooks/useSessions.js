import { useQuery } from "@tanstack/react-query";
import axiosInstance, { endpoints } from "src/utils/axios";
import { buildFilterParams } from "./useRequestLogs";

/**
 * Fetches paginated session aggregation data.
 *
 * @param {Object}  options
 * @param {Object}  [options.filters={}]    - camelCase filter values (same shape as useRequestLogs)
 * @param {number}  [options.page=1]        - 1-based page number
 * @param {number}  [options.pageSize=25]   - rows per page
 * @param {boolean} [options.enabled=true]  - controls whether the query fires
 * @returns {{ data: Object|undefined, isLoading: boolean, error: Error|null }}
 */
export default function useSessions({
  filters = {},
  page = 1,
  pageSize = 25,
  enabled = true,
} = {}) {
  return useQuery({
    queryKey: ["requestLogSessions", filters, page, pageSize],
    queryFn: async () => {
      const params = {
        ...buildFilterParams(filters),
        page,
        limit: pageSize,
      };

      const res = await axiosInstance.get(
        endpoints.gateway.requestLogSessions,
        { params },
      );
      return res.data;
    },
    enabled: Boolean(enabled),
  });
}
