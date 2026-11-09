import { useCallback, useEffect, useRef } from "react";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import useFalconStore from "../store/useFalconStore";
import { captureFalconRequest } from "../store/falconRequestGuard";
import { listSkills, fetchConnectors } from "./useFalconAPI";

// Full page and the mounted sidebar share a cache. Deduplicate the automatic
// landing load, including sidebar reopen and StrictMode effect replays.
let latestLoad = null;
function loadCatalog(force = false) {
  if (!force && latestLoad?.isCurrent()) return latestLoad.promise;
  const isCurrent = captureFalconRequest();
  const load = { isCurrent, promise: null };
  latestLoad = load;
  const canApply = () => latestLoad === load && isCurrent();
  load.promise = (async () => {
    let failed = false;
    try {
      const data = await listSkills();
      if (!canApply()) return;
      useFalconStore.getState().setSkills(data.results || data || []);
    } catch {
      // Optional catalog; do not retain old-scope data on failure.
      failed = true;
    }
    if (!canApply()) return;
    try {
      const data = await fetchConnectors();
      if (!canApply()) return;
      useFalconStore.getState().setConnectors(data.results || data || []);
    } catch {
      // Optional catalog.
      failed = true;
    }
    // Keep in-flight dedup until both endpoints settle, but retry partial or
    // failed loads on the next open. Never evict a newer load's cache.
    if (failed && latestLoad === load) latestLoad = null;
  })();
  return load.promise;
}

export default function useFalconCatalog(enabled = true) {
  const { currentWorkspaceId, isReady } = useWorkspace();
  const resetGeneration = useFalconStore((s) => s.resetGeneration);
  const activated = useRef(false);
  useEffect(() => {
    if (enabled) activated.current = true;
    // Recovery closes the sidebar, but its previously requested shared catalog
    // still needs one fresh load when the landing workspace becomes ready.
    if (activated.current && isReady && currentWorkspaceId) loadCatalog();
  }, [enabled, currentWorkspaceId, isReady, resetGeneration]);

  return useCallback(() => loadCatalog(true), []);
}
