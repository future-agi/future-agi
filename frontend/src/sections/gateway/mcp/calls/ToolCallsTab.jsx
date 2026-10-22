import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const ToolCallsTab = () => (
  <GatewayEmptyState
    title="Not in this release"
    description="Every MCP tool call, with its run, agent, outcome and cost."
  />
);

export default ToolCallsTab;
