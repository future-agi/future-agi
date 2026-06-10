import { useEffect } from "react";
import useFalconStore from "../store/useFalconStore";

/**
 * Consume agent-driven navigation (Phase 4C "take me there").
 *
 * The backend `navigate` WS event (emitted only for whitelist-validated
 * navigate_to_page tool calls) lands in useFalconStore.pendingNavigation;
 * this hook — mounted once by DashboardLayout — pushes the route and clears
 * the pending value FIRST, so each navigation is consumed exactly once even
 * if the push re-renders the layout.
 */
export default function usePendingNavigation(router) {
  const pendingNavigation = useFalconStore((s) => s.pendingNavigation);
  const clearPendingNavigation = useFalconStore(
    (s) => s.clearPendingNavigation,
  );

  useEffect(() => {
    if (pendingNavigation) {
      clearPendingNavigation();
      router.push(pendingNavigation);
    }
  }, [pendingNavigation, router, clearPendingNavigation]);
}
