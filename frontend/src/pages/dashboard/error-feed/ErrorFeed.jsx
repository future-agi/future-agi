import React from "react";
import ErrorFeedView from "./ErrorFeedView";

// License gating happens at the route level via CapabilityGate
// ("error_feed") — driven by /api/capabilities/, which understands
// deployment flavor, license state, and cloud plans.
export default function ErrorFeed() {
  return <ErrorFeedView />;
}
