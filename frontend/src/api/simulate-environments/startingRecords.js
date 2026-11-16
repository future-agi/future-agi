import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { draftsKey } from "src/api/simulate-environments/versions";

/**
 * A scenario's starting records: the seeded rows of the world its facts name, each looked up by its key and worded
 * by the server (held or missing), the rows it could start from instead, and the latest request to the builder to
 * rework it around one of them. The editor shows them and files that request; the builder's turn makes the change.
 */

const startingRecordsPath = (envId, scenarioKey) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/scenarios/{scenario_key}/starting-records/",
    { id: envId, scenario_key: scenarioKey },
  );

const reworkPath = (envId, scenarioKey) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/scenarios/{scenario_key}/starting-records/rework/",
    { id: envId, scenario_key: scenarioKey },
  );

// How often the panel asks again while the builder is still working on a request.
export const REWORK_POLL_MS = 10000;

/** @returns {Promise<import("src/generated/api-contracts/api.schemas").HarnessStartingRecordsApi>} */
export const getStartingRecords = async (envId, scenarioKey) =>
  (await axios.get(startingRecordsPath(envId, scenarioKey))).data;

export const reworkStartingRecord = async (
  envId,
  scenarioKey,
  { path, table, key },
) =>
  (await axios.post(reworkPath(envId, scenarioKey), { path, table, key })).data;

// Under the drafts, so whatever refreshes the drafts reads the records again.
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
    // While the builder works on a request, its state is asked again until the turn ends.
    refetchInterval: (query) =>
      query.state.data?.rework?.state === "pending" ? REWORK_POLL_MS : false,
    meta: { errorHandled: true },
  });
}

// Files the request; the read is asked again so the panel shows it pending. The caller shows a refusal.
export function useReworkStartingRecord(envId, scenarioKey) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (choice) => reworkStartingRecord(envId, scenarioKey, choice),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: startingRecordsKey(envId, scenarioKey),
      }),
  });
}
