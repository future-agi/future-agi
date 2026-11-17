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
import { useGatewayProbeState } from "src/sections/omega/shell/gatewayProbe";
import { isPrototypeMode } from "src/utils/prototype";

export function useDeploymentMode() {
  const { data, isLoading, isSuccess, refetch } = useQuery({
    queryKey: ["deployment-info"],
    queryFn: () => axios.get(endpoints.settings.v2.deploymentInfo),
    select: (res) => res.data?.result?.mode,
    staleTime: Infinity,
    retry: 1,
    // A failed read is not asked again each time a guard or page mounts:
    // the shell swaps its routes for the splash while this loads, so asking
    // on mount would remount them and ask again, without end.
    retryOnMount: false,
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
 * With VITE_OMEGA_CONSOLE unset the switch follows the gateway probe; the
 * caller (a sign-in page, the guard, the router) re-renders when it answers,
 * so a page drawn before the answer does not keep the old landing.
 */
export function usePostLoginPath() {
  const { isOSS } = useDeploymentMode();
  useGatewayProbeState();

  // Prototype mode (MSW mocks, local only) lands on the Datasets list: the
  // prototype is about datasets and experiments, and it has no real login
  // whose `redirectUrl` would need honouring. Real deployments unchanged.
  if (isPrototypeMode()) return paths.dashboard.develop;

  const returnTo = localStorage.getItem("redirectUrl");
  if (returnTo) return returnTo;
  if (isOmegaConsoleEnabled()) return paths.dashboard.omegaLanding;
  return isOSS ? paths.dashboard.getstarted : paths.dashboard.falconAI;
}
