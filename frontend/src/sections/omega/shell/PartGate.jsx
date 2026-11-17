import React from "react";
import PropTypes from "prop-types";
import { Navigate } from "react-router-dom";

import { LoadingScreen } from "src/components/loading-screen";
import { useCapabilities } from "src/hooks/useCapabilities";

import { isOmegaConsoleEnabled } from "./flags";
import { OMEGA_PART, ROLLOUT_STALE_MS, isPartReady } from "./rollout";

// The route-level gate for one part of a product (omega_rollout/readiness.py),
// inside the product's own ProductGate: a part that does not work today has no
// page, so its URL renders Future AGI's standard 404, exactly as an unknown
// URL does. While the answer loads: the host's loading screen, never the page.
export default function PartGate({ part, children }) {
  const { data, isLoading, isError } = useCapabilities({
    staleTime: isOmegaConsoleEnabled() ? ROLLOUT_STALE_MS : Infinity,
  });
  if (isLoading) return <LoadingScreen sx={undefined} />;
  if (isPartReady(data, part, { isLoading, isError })) return children;
  return <Navigate to="/404" replace />;
}

PartGate.propTypes = {
  part: PropTypes.oneOf(Object.values(OMEGA_PART)).isRequired,
  children: PropTypes.node,
};
