import { useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { draftsKey } from "src/api/simulate-environments/versions";

/**
 * A scenario's starting records, read-only: the seeded rows of the world its facts name, each looked up by its key
 * and worded by the server (held or missing). The editor only shows them.
 */

const startingRecordsPath = (envId, scenarioKey) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/scenarios/{scenario_key}/starting-records/",
    { id: envId, scenario_key: scenarioKey },
  );

/** @returns {Promise<import("src/generated/api-contracts/api.schemas").HarnessStartingRecordsApi>} */
export const getStartingRecords = async (envId, scenarioKey) =>
  (await axios.get(startingRecordsPath(envId, scenarioKey))).data;

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
    meta: { errorHandled: true },
  });
}
