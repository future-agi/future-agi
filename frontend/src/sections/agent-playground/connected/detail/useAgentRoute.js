import { useCallback } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useAgentDetail } from "src/api/agents/agentDetail";
import { resolveVersionId } from "./agentDetailTabs";

// The agent in the URL, the version the page shows (`?v=`, else active) and
// a way to pick another; picking the active version clears `?v=`.
export default function useAgentRoute() {
  const { agentId } = useParams();
  const [params, setParams] = useSearchParams();
  const query = useAgentDetail(agentId);
  const agent = query.data;
  const activeVersionId = agent?.activeVersionId ?? null;
  const versionId = agent
    ? resolveVersionId(params.get("v"), activeVersionId)
    : null;
  const selectVersion = useCallback(
    (id) => {
      const next = new URLSearchParams(params);
      if (id === activeVersionId) next.delete("v");
      else next.set("v", id);
      setParams(next, { replace: true });
    },
    [params, setParams, activeVersionId],
  );
  return { agentId, agent, query, versionId, selectVersion };
}
