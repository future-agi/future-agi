import { useCallback, useRef, useEffect } from "react";
import logger from "src/utils/logger";

/**
 * Debounce calls per key. flush(key) awaits pending/in-flight work and rejects
 * on failure; a failed call stays queued, so the next flush retries it unless a
 * newer call has replaced it.
 */
export const useDebouncedFunction = (
  mutationFn,
  delay = 1000,
  keySelector = (args) => args[0],
) => {
  const pendingRef = useRef(new Map());
  const inFlightRef = useRef(new Map());

  const flush = useCallback((key) => {
    const pending = pendingRef.current.get(key);
    if (pending) {
      clearTimeout(pending.timeoutId);
      pendingRef.current.delete(key);
      // Even after a failed save, a newer edit must get its own attempt.
      const previous = inFlightRef.current.get(key) || Promise.resolve();
      const promise = previous.then(pending.invoke, pending.invoke);
      inFlightRef.current.set(key, promise);
      promise.then(
        () => {
          if (inFlightRef.current.get(key) === promise) {
            inFlightRef.current.delete(key);
          }
        },
        (error) => {
          // Handle timer-path rejections without hiding them from flush callers.
          logger.error("Debounced function failed", error);
          if (inFlightRef.current.get(key) === promise) {
            inFlightRef.current.delete(key);
            // Keep the failed call queued: the next flush (e.g. Run) retries it.
            if (!pendingRef.current.has(key)) {
              pendingRef.current.set(key, { invoke: pending.invoke });
            }
          }
        },
      );
    }
    return inFlightRef.current.get(key) || Promise.resolve();
  }, []);

  const debouncedFn = useCallback(
    (...variables) => {
      const key = keySelector(variables);
      clearTimeout(pendingRef.current.get(key)?.timeoutId);
      pendingRef.current.set(key, {
        invoke: () => mutationFn(...variables),
        timeoutId: setTimeout(() => flush(key), delay),
      });
    },
    [mutationFn, delay, keySelector, flush],
  );
  debouncedFn.flush = flush;
  debouncedFn.isPending = (key) =>
    pendingRef.current.has(key) || inFlightRef.current.has(key);

  useEffect(() => {
    const pending = pendingRef.current;
    return () => {
      pending.forEach(({ timeoutId }) => clearTimeout(timeoutId));
      pending.clear();
    };
  }, []);

  return debouncedFn;
};
