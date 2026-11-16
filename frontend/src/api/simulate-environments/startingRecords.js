import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { harnessScenariosListKey } from "src/api/simulate-environments/scenariosHooks";
import {
  draftsKey,
  refreshDrafts,
} from "src/api/simulate-environments/versions";

// The server matches rows and refuses a bad choice with a field problem; the editor only shows and sends.

const startingRecordsPath = (envId, scenarioKey) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/scenarios/{scenario_key}/starting-records/",
    { id: envId, scenario_key: scenarioKey },
  );

export const getStartingRecords = async (envId, scenarioKey) =>
  (await axios.get(startingRecordsPath(envId, scenarioKey))).data;

export const chooseStartingRecord = async (
  envId,
  scenarioKey,
  { path, table, key },
) =>
  (
    await axios.put(startingRecordsPath(envId, scenarioKey), {
      path,
      table,
      key: String(key),
    })
  ).data;

// Under the drafts so a drafts refresh also refreshes the records.
export const startingRecordsKey = (envId, scenarioKey) => [
  ...draftsKey(envId),
  "starting-records",
  scenarioKey,
];

export function useStartingRecords(envId, scenarioKey) {
  return useQuery({
    queryKey: startingRecordsKey(envId, scenarioKey),
    queryFn: () => getStartingRecords(envId, scenarioKey),
    enabled: Boolean(envId && scenarioKey),
    meta: { errorHandled: true },
  });
}

export function useChooseStartingRecord(envId, scenarioKey) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (choice) => chooseStartingRecord(envId, scenarioKey, choice),
    onSuccess: (data) => {
      if (data)
        queryClient.setQueryData(startingRecordsKey(envId, scenarioKey), data);
      refreshDrafts(queryClient, envId);
      queryClient.invalidateQueries({
        queryKey: harnessScenariosListKey(envId),
      });
    },
  });
}
