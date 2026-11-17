import { useCallback, useEffect, useState } from "react";
import { readAdmissionKeys } from "../../providers/hooks/useOrgConfig";

const IDLE = { status: "idle", keys: [], error: null };

// The organization's keys, read only while an admission draft is open. A failed
// read is a state ("error"), never an empty list: an unreadable key list must
// not look like "there are no unbound keys".
export default function useAdmissionKeys(organizationId, enabled) {
  const [state, setState] = useState(IDLE);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setState(IDLE);
      return undefined;
    }
    let current = true;
    const controller = new AbortController();
    setState({ status: "loading", keys: [], error: null });
    readAdmissionKeys(organizationId, controller.signal)
      .then((keys) => {
        if (current) setState({ status: "ready", keys, error: null });
      })
      .catch((error) => {
        if (current) setState({ status: "error", keys: [], error });
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [organizationId, enabled, attempt]);

  const reload = useCallback(() => setAttempt((count) => count + 1), []);
  return { ...state, reload };
}
