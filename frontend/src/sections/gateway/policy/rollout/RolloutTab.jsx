import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const RolloutTab = () => (
  <GatewayEmptyState
    title="Not available on this gateway"
    description="This gateway runs no staged policy rollouts."
  />
);

export default RolloutTab;
