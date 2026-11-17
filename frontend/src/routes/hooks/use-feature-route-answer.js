import { useFeatureAllowed } from "src/hooks/useCapabilities";

/**
 * Whether the router registers a page that only some deployments run:
 * "allowed" or "denied" once the deployment's capabilities answer, and
 * "pending" before sign-in (`enabled` false; nothing is read), while they
 * load, and when they could not be read. A pending page keeps its URL and
 * its capability gate shows the loading or retry state; a denied one is no
 * route, so the app's 404 answers its URL.
 */
export function useFeatureRouteAnswer(featureId, { enabled = true } = {}) {
  const { allowed, isLoading, isError } = useFeatureAllowed(featureId, {
    enabled,
  });
  if (!enabled || isLoading || isError) return "pending";
  return allowed ? "allowed" : "denied";
}
