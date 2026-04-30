/**
 * Deployment mode hook — detects oss / ee / cloud from backend.
 *
 * Uses React Query cache (staleTime: Infinity) — fetches once, shared globally.
 * No Context/Provider needed.
 *
 * Usage:
 *   const { isOSS, isCloud, isEE } = useDeploymentMode();
 */

import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export function useDeploymentMode() {
  const { data, isLoading } = useQuery({
    queryKey: ["deployment-info"],
    queryFn: async () => {
      try {
        return await axios.get(endpoints.settings.v2.deploymentInfo);
      } catch (err) {
        // OSS builds without the ee/usage module don't register
        // /usage/v2/deployment-info/. Treat 404 as "we're on OSS" instead
        // of letting the error bubble — every consumer of this hook would
        // otherwise re-mount in a tight loop, causing visible UI flicker.
        if (err?.response?.status === 404) {
          return { data: { result: { mode: "oss" } } };
        }
        throw err;
      }
    },
    select: (res) => res.data?.result?.mode || "oss",
    staleTime: Infinity,
    retry: false,
  });

  const mode = data || "oss";

  return {
    mode,
    isCloud: mode === "cloud",
    isOSS: mode === "oss",
    isEE: mode === "ee",
    isLoading,
  };
}
