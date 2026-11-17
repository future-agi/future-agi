import { useCallback, useEffect, useRef } from "react";

// A saved score reaches the list API through CDC into ClickHouse, which can
// lag the write by up to ~8 s. The refresh fired straight after a save often
// reads the row before the score lands, and grid auto-refresh is off by
// default, so the label cell kept showing "-" until a page reload. Re-read a
// few times over ~10 s (the last read after the CDC bound), then stop.
export const ANNOTATION_FOLLOW_UP_REFRESH_DELAYS_MS = Object.freeze([
  2000, 5000, 9000,
]);

/**
 * Returns a stable callback for "scores were saved": it runs `refresh` now and
 * `refreshFollowUp` (defaults to `refresh`) after each follow-up delay. A new
 * save restarts the schedule; unmounting cancels it.
 *
 * @param {Function} refresh
 * @param {Function} [refreshFollowUp]
 * @returns {Function}
 */
export function useAnnotationGridRefresh(refresh, refreshFollowUp) {
  const refreshRef = useRef(refresh);
  const refreshFollowUpRef = useRef(refreshFollowUp);
  const timersRef = useRef([]);

  useEffect(() => {
    refreshRef.current = refresh;
    refreshFollowUpRef.current = refreshFollowUp;
  }, [refresh, refreshFollowUp]);

  const clearFollowUps = useCallback(() => {
    timersRef.current.forEach((timer) => clearTimeout(timer));
    timersRef.current = [];
  }, []);

  useEffect(() => clearFollowUps, [clearFollowUps]);

  return useCallback(() => {
    clearFollowUps();
    refreshRef.current?.();
    timersRef.current = ANNOTATION_FOLLOW_UP_REFRESH_DELAYS_MS.map((delay) =>
      setTimeout(() => {
        (refreshFollowUpRef.current || refreshRef.current)?.();
      }, delay),
    );
  }, [clearFollowUps]);
}
