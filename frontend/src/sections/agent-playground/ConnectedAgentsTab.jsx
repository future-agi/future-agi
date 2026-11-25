import React from "react";
import { useNavigate } from "react-router-dom";
import ConnectedAgentsView from "./connected/ConnectedAgentsView";
import useConnectAgentPath from "./useConnectAgentPath";

export default function ConnectedAgentsTab() {
  const navigate = useNavigate();
  const connectPath = useConnectAgentPath();
  return <ConnectedAgentsView onConnect={() => navigate(connectPath)} />;
}
