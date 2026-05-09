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
import { paths } from "src/routes/paths";

export function useDeploymentMode() {
  const { data, isLoading } = useQuery({
    queryKey: ["deployment-info"],
    queryFn: async () => {
      try {
        return await axios.get(endpoints.settings.v2.deploymentInfo);
      } catch (err) {
        // OSS deployments don't expose this endpoint — treat 404 as "oss" mode.
        if (err?.response?.status === 404) return { data: { result: { mode: "oss" } } };
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

export function usePostLoginPath() {
  const { isOSS } = useDeploymentMode();
  return isOSS ? paths.dashboard.develop : paths.dashboard.falconAI;
}
