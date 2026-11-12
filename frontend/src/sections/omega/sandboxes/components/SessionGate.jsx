import React from "react";
import PropTypes from "prop-types";

import ContentSkeleton from "src/components/content-skeleton";
import { OMEGA_STATE, useOmegaSession } from "src/sections/omega/shared";

import SandboxErrorState from "./SandboxErrorState";

/** Row-shaped placeholders where the rows will land, never a spinner. */
export function LoadingRows({ rows = 4, label = "Loading" }) {
  return (
    <ContentSkeleton
      variant="rows"
      rows={rows}
      label={label}
      data-testid="sandbox-loading"
    />
  );
}

LoadingRows.propTypes = { rows: PropTypes.number, label: PropTypes.string };

/**
 * Probe failures that say nothing about Sandboxes: the gateway's readiness
 * check did not answer OK (staging answered it 401 while the sandbox routes
 * worked), or the probe itself broke. The page still makes its own sandbox
 * call, which carries a minted assertion, and shows that call's answer.
 */
const PROBE_ONLY_FAILURES = new Set([
  "gateway_unavailable",
  "session_probe_failed",
]);

/**
 * Renders the tab when this browser session can use Omega in the selected
 * workspace (lane platform's useOmegaSession: a minted customer assertion
 * plus a live gateway), and also when only the gateway check failed: the
 * tab's own call then says what is wrong. Every other state is a calm panel.
 */
export default function SessionGate({
  children,
  fallbackFrame = (node) => node,
}) {
  const session = useOmegaSession();
  if (session.state === "checking") return fallbackFrame(<LoadingRows />);
  if (
    session.state === OMEGA_STATE.UNAVAILABLE &&
    PROBE_ONLY_FAILURES.has(session.code)
  ) {
    return children;
  }
  if (session.state !== OMEGA_STATE.READY) {
    return fallbackFrame(
      <SandboxErrorState
        error={{
          state: session.state,
          code: session.code,
          title: session.title ?? null,
          message: session.message,
          retryAfter: session.retryAfter ?? null,
        }}
        onRetry={session.recheck}
      />,
    );
  }
  return children;
}

SessionGate.propTypes = {
  children: PropTypes.node,
  /** Wraps the gate's own states (loading, refused, unavailable) in the page frame. */
  fallbackFrame: PropTypes.func,
};
