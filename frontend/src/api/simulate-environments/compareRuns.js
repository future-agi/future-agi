import { useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { compareRunsKeyPrefix } from "./runEvals";

/**
 * Two runs of one environment, scenario by scenario. Without `againstId` the
 * server compares with the previous completed run and names it in the answer
 * (`against_execution_id`, null when there is none). Not gated on versioning:
 * runs from before versioning compare too, with null versions.
 */

const comparePath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/compare/", { id: envId });

export const getRunComparison = async (envId, executionId, againstId) =>
  (
    await axios.get(comparePath(envId), {
      params: {
        execution_id: executionId,
        ...(againstId ? { against_execution_id: againstId } : {}),
      },
    })
  ).data;

export const compareRunsKey = (envId, executionId, againstId) => [
  ...compareRunsKeyPrefix,
  envId,
  executionId,
  againstId || null,
];

const errorStatus = (error) => error?.statusCode ?? error?.response?.status;
// A refused comparison answers the same way every time; only a server or
// network failure is worth asking again.
const retryServerFailures = (failureCount, error) => {
  const status = errorStatus(error);
  return (status === undefined || status >= 500) && failureCount < 2;
};

export function useCompareRuns(
  envId,
  executionId,
  againstId,
  { enabled = true } = {},
) {
  return useQuery({
    queryKey: compareRunsKey(envId, executionId, againstId),
    queryFn: () => getRunComparison(envId, executionId, againstId),
    enabled: Boolean(envId && executionId) && enabled,
    retry: retryServerFailures,
    staleTime: 1000 * 60 * 5,
    meta: { errorHandled: true },
  });
}
