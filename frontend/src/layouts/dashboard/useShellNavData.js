import { useMemo } from "react";
import { paths } from "src/routes/paths";
import { useFeatureAllowed } from "src/hooks/useCapabilities";
import { useNavData } from "./config-navigation";

// The app navigation as the shell shows it (sidebar, mini rail, top bar).
// The "Falcon AI" entry exists only where the deployment can run Falcon AI:
// capabilities.features.falcon_ai.allowed, the same rule as the Falcon AI
// launcher (FalconAIFab). Where it is not allowed, and while capabilities
// load or after they fail, the entry is left out instead of leading to the
// upgrade wall. A section left with no items is dropped; others are kept.
export function withoutFalconAI(sections) {
  return sections.flatMap((section) => {
    const items = section.items ?? [];
    const kept = items.filter((item) => item.path !== paths.dashboard.falconAI);
    if (kept.length === items.length) return [section];
    return kept.length > 0 ? [{ ...section, items: kept }] : [];
  });
}

export function useShellNavData() {
  const navData = useNavData();
  const { allowed } = useFeatureAllowed("falcon_ai");
  return useMemo(
    () => (allowed ? navData : withoutFalconAI(navData)),
    [allowed, navData],
  );
}
