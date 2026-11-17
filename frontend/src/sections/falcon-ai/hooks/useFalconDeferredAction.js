import { useCallback, useEffect, useRef } from "react";
import useFalconStore from "../store/useFalconStore";
import { captureFalconRequest } from "../store/falconRequestGuard";

// Capture at the user action, not when a delayed child method finally runs.
// Cancellation also invalidates callbacks already queued by the browser.
export default function useFalconDeferredAction() {
  const pending = useRef(new Set());
  useEffect(() => {
    const cancel = () => {
      pending.current.forEach((entry) => {
        entry.cancelled = true;
        clearTimeout(entry.timer);
      });
      pending.current.clear();
    };
    const unsubscribe = useFalconStore.subscribe((state, previous) => {
      if (state.resetGeneration !== previous.resetGeneration) cancel();
    });
    return () => {
      unsubscribe();
      cancel();
    };
  }, []);

  return useCallback((action, delay = 0, options) => {
    const isCurrent = captureFalconRequest(options);
    const entry = { cancelled: false, timer: null };
    entry.timer = setTimeout(() => {
      pending.current.delete(entry);
      if (!entry.cancelled && isCurrent()) action();
    }, delay);
    pending.current.add(entry);
  }, []);
}
