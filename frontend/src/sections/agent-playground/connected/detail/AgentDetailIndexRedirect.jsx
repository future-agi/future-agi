import React from "react";
import { Navigate, useLocation, useParams } from "react-router-dom";
import { agentDetailPath } from "./agentDetailTabs";

export default function AgentDetailIndexRedirect() {
  const { agentId } = useParams();
  const { search } = useLocation();
  return <Navigate to={`${agentDetailPath(agentId)}${search}`} replace />;
}
