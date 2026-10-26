import { useEffect, useRef, useState } from "react";

/**
 * Whether to show a loader for `loading`, without the flash a fast request
 * causes: nothing appears for a load that ends within `delay`, and a loader
 * that did appear stays at least `minDuration`, so it never blinks.
 */
export function useDelayedLoading(
  loading,
  { delay = 200, minDuration = 400 } = {},
) {
  const [shown, setShown] = useState(false);
  const shownAt = useRef(0);
  const timer = useRef(null);

  useEffect(() => {
    clearTimeout(timer.current);
    if (loading) {
      if (!shown) {
        timer.current = setTimeout(() => {
          shownAt.current = Date.now();
          setShown(true);
        }, delay);
      }
    } else if (shown) {
      const remaining = minDuration - (Date.now() - shownAt.current);
      if (remaining > 0)
        timer.current = setTimeout(() => setShown(false), remaining);
      else setShown(false);
    }
    return () => clearTimeout(timer.current);
  }, [loading, shown, delay, minDuration]);

  return shown;
}
