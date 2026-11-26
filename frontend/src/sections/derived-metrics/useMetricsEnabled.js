import { useCapabilities } from "src/hooks/useCapabilities";

// Share the existing capability query/cache; do not probe a metrics endpoint
// (those deliberately return 404 while the deployment rollout is off).
export function useMetricsEnabled() {
  const { data, isLoading, isError, refetch } = useCapabilities();
  return {
    enabled: !isLoading && !isError && data?.metrics_enabled === true,
    isLoading,
    isError,
    refetch,
  };
}
