import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";

import { OMEGA_STATE, useOmegaSession } from "src/sections/omega/shared";

import SandboxErrorState from "./SandboxErrorState";

export function LoadingRows({ rows = 4 }) {
  return (
    <Box
      data-testid="sandbox-loading"
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
 * Renders the tab only when this browser session can use Omega in the
 * selected workspace (lane platform's useOmegaSession: a minted customer
 * assertion plus a live gateway). Every other state is a calm panel.
 */
export default function SessionGate({
  children,
  fallbackFrame = (node) => node,
}) {
  const session = useOmegaSession();
  if (session.state === "checking") return fallbackFrame(<LoadingRows />);
  if (session.state !== OMEGA_STATE.READY) {
    return fallbackFrame(
      <SandboxErrorState
        error={{
          state: session.state,
          code: session.code,
          message: session.message,
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
