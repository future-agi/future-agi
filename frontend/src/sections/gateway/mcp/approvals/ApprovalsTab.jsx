import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const ApprovalsTab = () => (
  <GatewayEmptyState
    title="Not in this release"
    description="Tool calls waiting for an admin's approval, and the history of past decisions."
  />
);

export default ApprovalsTab;
