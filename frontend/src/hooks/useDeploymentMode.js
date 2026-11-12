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
import { isOmegaConsoleEnabled } from "src/sections/omega/shell/flags";
import { isPrototypeMode } from "src/utils/prototype";

export function useDeploymentMode() {
  const { data, isLoading, isSuccess, refetch } = useQuery({
    queryKey: ["deployment-info"],
    queryFn: () => axios.get(endpoints.settings.v2.deploymentInfo),
    select: (res) => res.data?.result?.mode,
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
    isConfirmed: isSuccess && ["cloud", "oss", "ee"].includes(data),
    refetch,
  };
}

/**
 * Where sign-in goes when no `returnTo` was asked for. With the Omega switch
 * on, the landing route decides once the workspace's capabilities answer
 * (Console Chat for a workspace admitted to the Console, DEMO-FIX-2 item 8;
 * sections/omega/shell/post-login-landing.js). Plain builds are unchanged.
 */
export function usePostLoginPath() {
  const { isOSS } = useDeploymentMode();

  // Prototype mode (MSW mocks, local only) lands on the Datasets list: the
  // prototype is about datasets and experiments, and it has no real login
  // whose `redirectUrl` would need honouring. Real deployments unchanged.
  if (isPrototypeMode()) return paths.dashboard.develop;

  const returnTo = localStorage.getItem("redirectUrl");
  if (returnTo) return returnTo;
  if (isOmegaConsoleEnabled()) return paths.dashboard.omegaLanding;
  return isOSS ? paths.dashboard.getstarted : paths.dashboard.falconAI;
}
