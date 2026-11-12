import { useCallback, useEffect, useState } from "react";
import { resolveAttachments } from "./client";

const MIN_USABLE_MS = 2000;
const RENEW_BEFORE_MS = 10000;

/**
 * Resolve gateway grants for at most 64 occurrences of one project.
 *
 * Nothing is requested until `enabled`. Grants are viewer- and session-bound,
 * so they are kept in component state only (no query cache). With `renew`
 * the grants are re-resolved shortly before the earliest expiry; images do
 * not renew because a loaded image needs no further bytes.
 *
 * @param {string} projectId
 * @param {Array<object>} requests F01 ResolveItem objects, in display order
 * @param {{enabled?: boolean, renew?: boolean}} [options]
 */
export default function useAttachmentGrants(
  projectId,
  requests,
  { enabled = true, renew = false } = {},
) {
  const requestKey = JSON.stringify(requests);
  const stateKey = `${projectId}|${enabled}|${requestKey}`;
  // Results are tagged with the request they answer, so a changed request
  // never renders the previous request's grants (or refetches its bytes)
  // during the render before the effect below marks it loading.
  const [state, setState] = useState({
    key: null,
    status: "idle",
    results: [],
  });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const items = JSON.parse(requestKey);
    if (!enabled || !items.length) {
      setState({ key: stateKey, status: "idle", results: [] });
      return undefined;
    }
    const controller = new AbortController();
    let timer;
    let stopped = false;
    const load = async () => {
      try {
        const results = await resolveAttachments(
          projectId,
          items,
          controller.signal,
        );
        if (stopped) return;
        setState({ key: stateKey, status: "ready", results });
        const expiries = results
          .filter((item) => item.status === "ready")
          .map((item) => Date.parse(item.expires_at));
        if (!renew || !expiries.length) return;
        const remaining = Math.min(...expiries) - Date.now();
        // Never spin on a grant that is already about to expire.
        if (remaining < MIN_USABLE_MS) {
          setState({ key: stateKey, status: "unavailable", results: [] });
          return;
        }
        timer = setTimeout(
          load,
          Math.min(2147483647, Math.max(1000, remaining - RENEW_BEFORE_MS)),
        );
      } catch {
        if (!stopped)
          setState({ key: stateKey, status: "unavailable", results: [] });
      }
    };
    setState({ key: stateKey, status: "loading", results: [] });
    void load();
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [stateKey, projectId, requestKey, enabled, renew, attempt]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  if (state.key !== stateKey) {
    const active = enabled && requests.length > 0;
    return { status: active ? "loading" : "idle", results: [], retry };
  }
  return { status: state.status, results: state.results, retry };
}
