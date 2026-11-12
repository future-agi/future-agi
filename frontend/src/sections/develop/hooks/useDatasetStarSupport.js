import { useCapabilities } from "src/hooks/useCapabilities";
import { datasetCaps } from "../realBackendCaps";

/** Build visibility is not proof that this server can store personal stars. */
export default function useDatasetStarSupport() {
  const caps = datasetCaps();
  const { data, isError } = useCapabilities({
    enabled: caps.real && caps.listStars,
    staleTime: 30 * 1000,
  });
  const available = Boolean(
    caps.listStars &&
      (!caps.real || (!isError && data?.features?.dataset_stars?.allowed === true)),
  );
  const checking = caps.real && caps.listStars && !data && !isError;
  return {
    available,
    // Only a completed denial clears a stale Starred link, not a failed read.
    unavailable: Boolean(
      caps.real && (!caps.listStars || (data && !isError && !available)),
    ),
    reason: available
      ? null
      : isError
        ? "Could not check star availability. Reload to try again."
        : checking
          ? "Checking star availability…"
          : "Stars are not available on this server.",
  };
}
