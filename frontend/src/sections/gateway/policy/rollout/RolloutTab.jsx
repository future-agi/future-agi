import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const RolloutTab = () => (
  <GatewayEmptyState
    title="Not in this release"
    description="Staged rollouts of a candidate policy, with a stable-versus-candidate report, promote and abort."
  />
);

export default RolloutTab;
