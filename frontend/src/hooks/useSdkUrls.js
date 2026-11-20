/**
 * The URLs an SDK outside the stack sets, as the backend gives signed-in
 * users (the `sdk_urls` of /tracer/project/project_sdk_code/, the same values
 * as the in-app SDK snippet): `apiUrl` for FI_BASE_URL and `tracesUrl` for
 * FI_TRACES_URL.
 *
 * Both are null on Future AGI Cloud, where the SDK defaults apply, while the
 * fetch is in flight, and when `enabled` is false (before sign-in: the API
 * URL can name an internal host, so it is never fetched anonymously).
 *
 * Usage:
 *   const { apiUrl, tracesUrl } = useSdkUrls({ enabled: authenticated });
 */

import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const cleanUrl = (url) =>
  typeof url === "string" && url.trim() ? url.trim().replace(/\/+$/, "") : null;

export function useSdkUrls({ enabled = true } = {}) {
  const { data, isLoading } = useQuery({
    queryKey: ["tracer", "sdk-urls"],
    queryFn: () =>
      axios.get(endpoints.project.getCodeBlockTracer, {
        params: { project_type: "observe" },
      }),
    select: (res) => res.data?.result?.sdk_urls ?? null,
    enabled,
    staleTime: Infinity,
    retry: 1,
  });

  return {
    isLoading: enabled && isLoading,
    apiUrl: cleanUrl(data?.fi_base_url),
    tracesUrl: cleanUrl(data?.fi_traces_url),
  };
}
