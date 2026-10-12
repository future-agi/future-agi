import { useQuery } from "@tanstack/react-query";
import { apiPath } from "src/api/contracts/api-surface";
import axios from "src/utils/axios";

export const EDITION_QUERY_KEY = ["edition"];

/**
 * GET /api/edition/: the self-hosted edition (community or enterprise),
 * Community limits with current usage and, for admins, the licence status.
 * Cloud answers `{ edition: "cloud" }`.
 */
export function useEdition({ enabled = true } = {}) {
  return useQuery({
    queryKey: EDITION_QUERY_KEY,
    queryFn: () => axios.get(apiPath("/api/edition/")),
    select: (res) => res.data?.result,
    enabled,
    retry: 1,
  });
}
