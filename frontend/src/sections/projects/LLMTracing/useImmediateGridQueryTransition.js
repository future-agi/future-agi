import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { isGridApiLive } from "src/utils/gridApi";

// AG Grid intentionally debounces server-side block loads. Show feedback in
// the same paint as a semantic query change, then hand ownership to the real
// page-zero request. The timeout prevents URL/config hydration from leaving an
// overlay behind if AG Grid replaces a datasource without requesting it.
const QUERY_TRANSITION_HANDOFF_TIMEOUT_MS = 1000;

export default function useImmediateGridQueryTransition({
  enabled,
  filterRequestKey,
  gridRef,
  resetPagination,
}) {
  const [transitionLoading, setTransitionLoading] = useState(false);
  const previousFilterRequestKeyRef = useRef(filterRequestKey);
  const pendingFilterRequestKeyRef = useRef(null);
  const handoffTimerRef = useRef(null);

  const clearHandoffTimer = useCallback(() => {
    if (handoffTimerRef.current !== null) {
      window.clearTimeout(handoffTimerRef.current);
      handoffTimerRef.current = null;
    }
  }, []);

  useEffect(() => clearHandoffTimer, [clearHandoffTimer]);

  useLayoutEffect(() => {
    if (previousFilterRequestKeyRef.current === filterRequestKey) return;

    previousFilterRequestKeyRef.current = filterRequestKey;
    resetPagination();
    clearHandoffTimer();
    pendingFilterRequestKeyRef.current = null;
    setTransitionLoading(false);

    if (!enabled || !isGridApiLive(gridRef?.current?.api)) return;

    pendingFilterRequestKeyRef.current = filterRequestKey;
    setTransitionLoading(true);
    handoffTimerRef.current = window.setTimeout(() => {
      if (pendingFilterRequestKeyRef.current !== filterRequestKey) return;
      pendingFilterRequestKeyRef.current = null;
      handoffTimerRef.current = null;
      setTransitionLoading(false);
    }, QUERY_TRANSITION_HANDOFF_TIMEOUT_MS);
  }, [clearHandoffTimer, enabled, filterRequestKey, gridRef, resetPagination]);

  const handoffToFirstPageRequest = useCallback(
    (requestFilterKey) => {
      if (pendingFilterRequestKeyRef.current !== requestFilterKey) return;
      pendingFilterRequestKeyRef.current = null;
      clearHandoffTimer();
      setTransitionLoading(false);
    },
    [clearHandoffTimer],
  );

  return { handoffToFirstPageRequest, transitionLoading };
}
