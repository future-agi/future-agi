// W6-2 (A3): the wizard's agent cards with what their version uses
// (agentUses.js), read through the version-detail query the estimate
// already makes (same query key, so no second request).
import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { withAgentUses } from "./agentUses";

export function useCardsWithAgentUses(cards) {
  const list = cards ?? [];
  const agents = list.filter(
    (c) => c?.targetType === "agent" && c?.agentId && c?.agentVersion,
  );
  const details = useQueries({
    queries: agents.map((c) => ({
      queryKey: [
        "agent-playground",
        "version-detail",
        c.agentId,
        c.agentVersion,
      ],
      queryFn: () =>
        axios.get(
          endpoints.agentPlayground.versionDetail(c.agentId, c.agentVersion),
        ),
      select: (res) => res.data?.result,
      staleTime: 60_000,
    })),
  });
  const versions = details.map((d) => d.data).filter(Boolean);
  const key = versions.map((v) => `${v.id}:${v.nodes?.length ?? 0}`).join("|");
  return useMemo(
    () => withAgentUses(list, new Map(versions.map((v) => [v.id, v]))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [list, key],
  );
}
