import React from "react";
import GatewayEmptyState from "../../components/GatewayEmptyState";

const ConnectTab = () => (
  <GatewayEmptyState
    title="Not in this release"
    description="The URL of each virtual server, with copy-paste setup for MCP clients such as Claude, Cursor, VS Code and Codex."
  />
);

export default ConnectTab;
