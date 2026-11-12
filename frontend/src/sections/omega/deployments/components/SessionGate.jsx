import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";

import { OMEGA_STATE, useOmegaSession } from "src/sections/omega/shared";

import { isRateLimited, rateLimitDelay } from "../lib/rateLimit";
import DeploymentErrorState from "./DeploymentErrorState";

// The gate's probe is the page's only Omega call before the tab renders, so
// after a rate-limited refusal it asks again by itself, never sooner than this.
const RECHECK_FLOOR_MS = 15_000;

export function LoadingRows({ rows = 4 }) {
  return (
    <Box
      data-testid="deployment-loading"
      sx={{ display: "flex", flexDirection: "column", gap: 1 }}
    >
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} variant="rounded" height={44} />
      ))}
    </Box>
  );
}

LoadingRows.propTypes = { rows: PropTypes.number };

/**
 * After a rate-limited refusal (the session exchange's 120 a minute), probe
 * again on a backoff (lib/rateLimit.js: at least 15 s, Retry-After honoured,
 * doubling while the limit holds, at most a minute) and keep that refusal on
 * screen while the probe runs, so the tab comes back by itself once the limit
 * clears and never flashes skeletons in between. Any other refusal waits for
 * the person, as before. `probes` counts the automatic re-checks: a probe that
 * answers at once can render the same refusal with no "checking" in between,
 * and the count still schedules the next wait.
 */
function useRateLimitRecovery({
  state,
  code,
  title,
  message,
  retryAfter,
  recheck,
}) {
  const [held, setHeld] = useState(null);
  const [probes, setProbes] = useState(0);
  const streak = useRef(0);
  useEffect(() => {
    if (state === "checking") return undefined;
    if (state === OMEGA_STATE.READY || !isRateLimited({ code })) {
      streak.current = 0;
      setHeld(null);
      return undefined;
    }
    streak.current += 1;
    setHeld({ state, code, title, message, retryAfter });
    const wait = rateLimitDelay(
      { retryAfter },
      streak.current,
      RECHECK_FLOOR_MS,
    );
    const timer = setTimeout(() => {
      setProbes((count) => count + 1);
      recheck();
    }, wait);
    return () => clearTimeout(timer);
  }, [state, code, title, message, retryAfter, recheck, probes]);
  return held;
}

/**
 * Renders the tab only when this browser session can use Omega in the
 * selected workspace (lane platform's useOmegaSession: a minted customer
 * assertion plus a live gateway). Every other state is a calm panel.
 */
export default function SessionGate({
  children,
  fallbackFrame = (node) => node,
}) {
  const session = useOmegaSession();
  const held = useRateLimitRecovery(session);
  const checking = session.state === "checking";
  const refusal = checking
    ? held
    : session.state === OMEGA_STATE.READY
      ? null
      : session;
  if (refusal) {
    return fallbackFrame(
      <DeploymentErrorState
        error={{
          state: refusal.state,
          code: refusal.code,
          title: refusal.title ?? null,
          message: refusal.message,
          retryAfter: refusal.retryAfter ?? null,
        }}
        onRetry={session.recheck}
      />,
    );
  }
  if (checking) return fallbackFrame(<LoadingRows />);
  return children;
}

SessionGate.propTypes = {
  children: PropTypes.node,
  /** Wraps the gate's own states (loading, refused, unavailable) in the page frame. */
  fallbackFrame: PropTypes.func,
};
