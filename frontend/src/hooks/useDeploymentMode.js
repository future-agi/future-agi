/**
 * Deployment mode hook — detects oss / ee / cloud from backend.
 *
 * Uses React Query cache (staleTime: Infinity) — fetches once, shared globally.
 * No Context/Provider needed.
 *
 * Usage:
 *   const { isOSS, isCloud, isEE, isSelfHosted } = useDeploymentMode();
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { paths } from "src/routes/paths";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";

// Landings a self-hosted install only has with a usable Enterprise licence.
const LICENSED_LANDINGS = [paths.dashboard.falconAI];

export function useDeploymentMode() {
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
    // Where the install runs, licensed or not (TH-8084). Onboarding (signup,
    // invite links, the first-run checks) follows this, not the licence.
    isSelfHosted: mode !== "cloud",
    isLoading,
    isSuccess,
  };
}

export function usePostLoginPath() {
  const { isOSS, isCloud, isSuccess } = useDeploymentMode();
  const queryClient = useQueryClient();
  // One-shot (TH-8005): read on every render so a destination the auth flow
  // has consumed (removed) is never replayed by the persistent Router. Each
  // caller navigates with the value from its render, then removes it.
  const returnTo = localStorage.getItem("redirectUrl");

  // Until the deployment is known, and on Cloud, as before.
  if (!isSuccess || isCloud) {
    if (returnTo) return returnTo;
    return isOSS ? paths.dashboard.getstarted : paths.dashboard.falconAI;
  }

  // Self-hosted: land on an included product. Falcon AI needs a usable
  // licence; only an already cached /api/capabilities/ answer is consulted,
  // so the login page makes no authenticated request.
  const capabilities = queryClient.getQueryData(CAPABILITIES_QUERY_KEY);
  const falconAllowed =
    capabilities?.data?.features?.falcon_ai?.allowed === true;
  const gated = (path) =>
    LICENSED_LANDINGS.some((landing) => path.startsWith(landing));
  if (returnTo && (falconAllowed || !gated(returnTo))) return returnTo;
  return falconAllowed ? paths.dashboard.falconAI : paths.dashboard.getstarted;
}
