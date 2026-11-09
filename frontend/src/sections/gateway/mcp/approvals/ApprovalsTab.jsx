import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const ApprovalsTab = () => (
  <GatewayEmptyState
    title="Not available on this gateway"
    description="This gateway does not hold tool calls for approval."
  />
);

export default ApprovalsTab;
