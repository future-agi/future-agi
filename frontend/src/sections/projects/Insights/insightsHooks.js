import { useCallback, useEffect, useRef, useState } from "react";
import {
  insightsCreditRefusal,
  insightsErrorMessage,
  isAbort,
} from "./insightsApi";

export function useDebouncedValue(value, delay = 350) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

/** Whole seconds since `since` (a Date, ISO string or ms), ticking each second. */
export function useElapsedSeconds(since, active = true) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active, since]);
  if (!since) return 0;
  const start =
    since instanceof Date ? since.getTime() : new Date(since).getTime();
  return Number.isFinite(start)
    ? Math.max(0, Math.round((now - start) / 1000))
    : 0;
}

/**
 * A cancellable request (draft, preview, start): {state, run, cancel, reset}
 * where state is {status: "idle" | "busy" | "done" | "error", data, error,
 * billing, startedAt}. A newer call or cancel aborts the one in flight. A
 * fault on the server's side reads `retryMessage` (or a general "try again").
 */
export function useCancellable(fallbackMessage, retryMessage) {
  const [state, setState] = useState({ status: "idle" });
  const controller = useRef(null);

  const cancel = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
    setState((current) =>
      current.status === "busy" ? { status: "idle" } : current,
    );
  }, []);

  const run = useCallback(
    async (task) => {
      controller.current?.abort();
      const own = new AbortController();
      controller.current = own;
      setState({ status: "busy", startedAt: Date.now() });
      try {
        const data = await task(own.signal);
        if (controller.current !== own) return undefined;
        controller.current = null;
        setState({ status: "done", data });
        return data;
      } catch (error) {
        if (controller.current !== own || isAbort(error)) return undefined;
        controller.current = null;
        setState({
          status: "error",
          error: insightsErrorMessage(error, fallbackMessage, retryMessage),
          // Refused for credits: the way forward is billing.
          billing: Boolean(insightsCreditRefusal(error)),
        });
        return undefined;
      }
    },
    [fallbackMessage, retryMessage],
  );

  const reset = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
    setState({ status: "idle" });
  }, []);

  useEffect(() => () => controller.current?.abort(), []);

  return { state, run, cancel, reset };
}
