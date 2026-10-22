import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const TrustTab = () => (
  <GatewayEmptyState
    title="Not in this release"
    description="Changes to a server's tools that are waiting for review, with a diff to approve or reject."
  />
);

export default TrustTab;
