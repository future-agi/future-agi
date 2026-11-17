import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const RegistryTab = () => (
  <GatewayEmptyState
    title="Not available on this gateway"
    description="This gateway has no MCP server registry."
  />
);

export default RegistryTab;
