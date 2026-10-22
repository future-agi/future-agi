import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const VirtualServersTab = () => (
  <GatewayEmptyState
    title="Not in this release"
    description="Virtual servers that combine tools from several upstream servers, with their access rules, approvals and budgets."
  />
);

export default VirtualServersTab;
