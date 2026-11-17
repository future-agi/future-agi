// options-truth L5 G3 (one estimator): what the run engine prices a try
// with, read from the API the wizard already uses: each agent card's
// version node models (graph version detail, llm nodes in graph order) and
// the org's custom models' stored per-1M prices. Every estimate surface
// (footer, Review, Details) reads them through useEstimateInputs.
import { useMemo } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { withEngineInputs } from "./estimateInputs";

export function useEngineCostInputs(promptConfig) {
  const cards = promptConfig ?? [];
  const agents = cards.filter(
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
  const custom = useQuery({
    queryKey: ["estimate", "custom-models"],
    queryFn: () => axios.get(endpoints.model.list),
    select: (res) => res.data?.result?.results ?? res.data?.results ?? [],
    staleTime: 60_000,
  });
  const versions = details.map((d) => d.data).filter(Boolean);
  const versionKey = versions.map((v) => v.id).join("|");
  const customKey = (custom.data ?? []).map((m) => m.id).join("|");
  return useMemo(
    () => withEngineInputs(cards, { versions, customModels: custom.data }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cards, versionKey, customKey],
  );
}
