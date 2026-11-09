import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useApiKeysStatus } from "src/api/model/api-keys";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { DECISION_PROVIDERS, pickerRowsOf } from "./decisionModels";

/**
 * The workspace's decision models (GET /v1/decisions/models through the
 * backend) as picker rows: every preset model, ready when the listing
 * returned it, else with the provider whose key is missing. The listing
 * refetches when a provider key is saved (the key status changes).
 */
export function useDecisionModels({ enabled = true } = {}) {
  const available = isSurfaceShown("models.decision");
  const active = enabled && available;
  const { data: keys } = useApiKeysStatus({ enabled: active });
  const signature = Object.keys(DECISION_PROVIDERS)
    .map(
      (p) =>
        `${p}:${(keys ?? []).some((k) => k.provider === p && k.hasKey) ? 1 : 0}`,
    )
    .join(",");
  const q = useQuery({
    queryKey: ["decision-models", signature],
    queryFn: () => axios.get(endpoints.develop.decisionModels),
    select: (res) => res?.data?.result ?? { object: "list", data: [] },
    enabled: active,
    staleTime: 60_000,
  });
  return {
    listing: available ? q.data ?? null : null,
    rows: available ? pickerRowsOf(q.data ?? { data: [] }) : [],
    isLoading: active && q.isLoading,
    isError: active && q.isError,
    refetch: (options = undefined) =>
      active ? q.refetch(options) : Promise.resolve(null),
  };
}
