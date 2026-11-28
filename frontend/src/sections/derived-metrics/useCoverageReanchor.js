import { useEffect, useRef } from "react";
import { coverageReadableAt } from "./metricTime";

// A re-anchored window must end after the first period of values; a moment
// past it absorbs timer and clock jitter.
const SETTLE_MS = 2000;

/** A preset window is fixed when it is chosen. A metric that started with no
 * history has nothing to read until its first whole 15-minute period ends, so
 * if that happens while the window is shown, call reanchor() once then: the
 * page reads a fresh window that holds it, without a reload. Pass no
 * reanchor for a window the user chose explicitly. */
export default function useCoverageReanchor(window, definitions, reanchor) {
  const latest = useRef(reanchor);
  latest.current = reanchor;
  const due = (definitions || [])
    .map((definition) => coverageReadableAt(window, definition)?.getTime())
    .filter(Number.isFinite);
  const at = due.length ? Math.min(...due) : null;
  const enabled = Boolean(reanchor);
  useEffect(() => {
    if (at == null || !enabled) return undefined;
    const timer = setTimeout(
      () => latest.current?.(),
      Math.max(0, at - Date.now()) + SETTLE_MS,
    );
    return () => clearTimeout(timer);
  }, [at, enabled]);
}
