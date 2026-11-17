import { useCallback, useEffect, useRef, useState } from "react";

import { useWorkspace } from "src/contexts/WorkspaceContext";

import { DEFAULT_OMEGA_API_BASE, resolveOmegaApiBase } from "./omegaClient";
import { createAssertionMinter } from "./sessionAssertion";
import { OMEGA_STATE, OmegaError, defaultMessage } from "./omegaStates";

const defaultMint = createAssertionMinter();
const defaultBase =
  resolveOmegaApiBase(import.meta.env?.VITE_OMEGA_API_BASE) ||
  DEFAULT_OMEGA_API_BASE;

/**
 * Probe whether this browser session can use Omega in the selected workspace:
 * the backend must mint a customer assertion (refuses viewers, signed-out
 * sessions and unconfigured delegation) and the gateway must be live. The
 * probe assertion is discarded unused; it expires in 45 seconds.
 */
export async function probeOmegaSession({
  mintAssertion = defaultMint,
  fetchImpl = (...a) => globalThis.fetch(...a),
  basePath = defaultBase,
} = {}) {
  const gateway = fetchImpl(`${basePath}/readyz`, {
    credentials: "omit",
    headers: { Accept: "application/json" },
  })
    .then((response) => response.ok)
    .catch(() => false);
  try {
    await mintAssertion();
  } catch (error) {
    if (error instanceof OmegaError)
      return {
        state: error.state,
        code: error.code,
        title: error.title ?? null,
        message: error.message,
        retryAfter: error.retryAfter ?? null,
      };
    return {
      state: OMEGA_STATE.UNAVAILABLE,
      code: "session_probe_failed",
      title: "Omega isn't responding",
      message: defaultMessage(OMEGA_STATE.UNAVAILABLE),
    };
  }
  if (!(await gateway)) {
    return {
      state: OMEGA_STATE.UNAVAILABLE,
      code: "gateway_unavailable",
      title: "Omega isn't responding",
      message: defaultMessage(OMEGA_STATE.UNAVAILABLE),
    };
  }
  return { state: OMEGA_STATE.READY, code: null, message: null };
}

/**
 * useOmegaSession() -> { state, code, title, message, workspaceId, recheck }
 *
 * `title` and `message` are Future AGI copy (omegaReasons.js).
 *
 * `state` is "checking" until the first probe finishes, then one of
 * OMEGA_STATE. It re-probes when the selected workspace changes; a stale
 * probe for a previous workspace never overwrites the current one.
 */
export function useOmegaSession({ probe = probeOmegaSession } = {}) {
  const { currentWorkspaceId } = useWorkspace();
  const [status, setStatus] = useState({
    state: "checking",
    code: null,
    message: null,
  });
  const generation = useRef(0);

  const recheck = useCallback(async () => {
    generation.current += 1;
    const mine = generation.current;
    setStatus({ state: "checking", code: null, message: null });
    const result = await probe();
    if (mine === generation.current) setStatus(result);
    return result;
  }, [probe]);

  useEffect(() => {
    recheck();
    return () => {
      generation.current += 1;
    };
  }, [recheck, currentWorkspaceId]);

  return { ...status, workspaceId: currentWorkspaceId ?? null, recheck };
}
