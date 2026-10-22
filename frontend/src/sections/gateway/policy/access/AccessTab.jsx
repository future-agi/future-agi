import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const AccessTab = () => (
  <GatewayEmptyState
    title="Not in this release"
    description="Which keys may reach which providers and models, managed-model allow and deny lists, and tool-call guardrails."
  />
);

export default AccessTab;
