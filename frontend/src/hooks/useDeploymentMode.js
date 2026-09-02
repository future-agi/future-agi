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
  /*
    LOCAL DEV OVERRIDE — dev.api.futureagi.com is currently 502 and the
    real fetch keeps the whole app on the splash screen. Short-circuit
    to `cloud` mode so guards resolve immediately and the frontend
    boots against its own mocks/localStorage. Revert before shipping.
  */
  return {
    mode: "cloud",
    isCloud: true,
    isOSS: false,
    isEE: false,
    isLoading: false,
    isSuccess: true,
  };

  // eslint-disable-next-line no-unreachable
  const { data, isLoading, isSuccess } = useQuery({
    queryKey: ["deployment-info"],
    queryFn: () => axios.get(endpoints.settings.v2.deploymentInfo),
    select: (res) => res.data?.result?.mode || "oss",
    staleTime: Infinity,
    retry: 1,
  });

  const mode = data || "oss";

  return {
    mode,
    isCloud: mode === "cloud",
    isOSS: mode === "oss",
    isEE: mode === "ee",
    isLoading,
    isSuccess,
  };
}

export function usePostLoginPath() {
  const { isOSS } = useDeploymentMode();

  const returnTo = localStorage.getItem("redirectUrl");
  if (returnTo) return returnTo;
  return isOSS ? paths.dashboard.getstarted : paths.dashboard.falconAI;
}
