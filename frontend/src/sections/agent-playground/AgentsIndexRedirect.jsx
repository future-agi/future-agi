import React from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { agentsTabPath, resolveAgentsTab } from "./agentsTabs";

/**
 * The bare Agents page opens Connected agents. Older links that carry the
 * tab as `?tab=` land on that tab's path, keeping any other params.
 */
export default function AgentsIndexRedirect() {
  const [params] = useSearchParams();
  const tab = resolveAgentsTab(params.get("tab"));
  const rest = new URLSearchParams(params);
  rest.delete("tab");
  const search = rest.toString();
  return (
    <Navigate
      to={`${agentsTabPath(tab)}${search ? `?${search}` : ""}`}
      replace
    />
  );
}
