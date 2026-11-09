import { useMemo } from "react";
import { CAPABILITY, useFeatureAllowed } from "src/hooks/useCapabilities";
import { offeredPlatforms } from "./constants";

// The platforms the Integrations page and the Add Integration wizard offer on
// this deployment. While its capabilities load, or when they fail to load, a
// platform that needs one is not offered.
export default function useOfferedPlatforms() {
  const { allowed: errorFeedAllowed } = useFeatureAllowed(
    CAPABILITY.ERROR_FEED,
  );
  return useMemo(
    () =>
      offeredPlatforms(
        (capability) =>
          capability === CAPABILITY.ERROR_FEED && errorFeedAllowed,
      ),
    [errorFeedAllowed],
  );
}
