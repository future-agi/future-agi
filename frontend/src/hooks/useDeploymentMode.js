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

// Whether an email actually reaches the person it was sent to.
//   none      no mail server set up, nothing is sent
//   provider  real mail server, it arrives
export const EMAIL_DELIVERY = {
  NONE: "none",
  PROVIDER: "provider",
};

export function useDeploymentMode() {
  const { data, isLoading, isSuccess } = useQuery({
    queryKey: ["deployment-info"],
    queryFn: () => axios.get(endpoints.settings.v2.deploymentInfo),
    select: (res) => ({
      mode: res.data?.result?.mode || "oss",
      // Absent until the backend adds it. Default to "none" rather than
      // "provider": showing "check your email" to someone who will never
      // receive one strands them, whereas the CLI path always works.
      emailDelivery: res.data?.result?.email_delivery || EMAIL_DELIVERY.NONE,
    }),
    staleTime: Infinity,
    retry: 1,
  });

  const mode = data?.mode || "oss";
  const emailDelivery = data?.emailDelivery || EMAIL_DELIVERY.NONE;

  return {
    mode,
    isCloud: mode === "cloud",
    isOSS: mode === "oss",
    isEE: mode === "ee",
    emailDelivery,
    // Only a real provider actually delivers to the recipient.
    canDeliverEmail: emailDelivery === EMAIL_DELIVERY.PROVIDER,
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
