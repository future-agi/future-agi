import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { harnessScenariosListKey } from "./scenariosHooks";

const tagsPath = (envId) =>
  apiPath(
    "/simulate/api/harness-environments/{environment_id}/scenario-tags/",
    {
      environment_id: envId,
    },
  );

const scenarioTagsPath = (envId, scenarioKey) =>
  apiPath(
    "/simulate/api/harness-environments/{environment_id}/scenario-tags/scenarios/{scenario_key}/",
    { environment_id: envId, scenario_key: scenarioKey },
  );

const changePath = (envId) =>
  apiPath(
    "/simulate/api/harness-environments/{environment_id}/scenario-tags/change/",
    { environment_id: envId },
  );

export const environmentTagsKey = (envId) => [
  "harness-environment",
  envId,
  "scenario-tags",
];

const unwrap = (data) => data?.result ?? data;

export function useEnvironmentTags(envId, { enabled = true } = {}) {
  return useQuery({
    queryKey: environmentTagsKey(envId),
    queryFn: async () =>
      unwrap((await axios.get(tagsPath(envId))).data)?.tags ?? [],
    enabled: Boolean(envId) && enabled,
    staleTime: 30_000,
    retry: false,
    meta: { errorHandled: true },
  });
}

function useRefreshAfterTagging(envId) {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({
        queryKey: harnessScenariosListKey(envId),
      }),
      queryClient.invalidateQueries({ queryKey: environmentTagsKey(envId) }),
    ]);
}

export function useSetScenarioTags(envId) {
  const refresh = useRefreshAfterTagging(envId);
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: async ({ scenarioKey, tags }) =>
      unwrap(
        (await axios.put(scenarioTagsPath(envId, scenarioKey), { tags })).data,
      ),
    onSettled: refresh,
  });
}

export function useChangeScenarioTags(envId) {
  const refresh = useRefreshAfterTagging(envId);
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: async ({ scenarioKeys, add = [], remove = [] }) =>
      unwrap(
        (
          await axios.post(changePath(envId), {
            scenario_keys: scenarioKeys,
            add,
            remove,
          })
        ).data,
      ),
    onSettled: refresh,
  });
}
