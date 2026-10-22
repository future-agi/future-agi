import React from "react";
import { useNavigate } from "react-router-dom";
import ConnectedAgentsView from "./connected/ConnectedAgentsView";
import { CONNECT_AGENT_PATH } from "./agentsTabs";

export default function ConnectedAgentsTab() {
  const navigate = useNavigate();
  return <ConnectedAgentsView onConnect={() => navigate(CONNECT_AGENT_PATH)} />;
}
