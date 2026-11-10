import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { VERSION_PIECES, versionPickFields } from "./versions";

/**
 * Run readiness for an agent version, environment version and scenario
 * version together (the pair): what can run, what is blocked and how to fix
 * it. The report is worked out on every read; Re-check starts a new check of
 * the pair, and while it runs the report is read again every 15 seconds.
 */

const CHECK_POLL_MS = 15000;

const capabilitiesPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/capabilities/", {
    id: envId,
  });
const recheckPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/capabilities/recheck/", {
    id: envId,
  });
const casesPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/capabilities/cases/", {
    id: envId,
  });

const errorStatus = (error) => error?.statusCode ?? error?.response?.status;
const retryServerFailures = (failureCount, error) => {
  const status = errorStatus(error);
  return (status === undefined || status >= 500) && failureCount < 2;
};

// A pair check is running, or the agent's own start check (after a retry).
const stillChecking = (report) =>
  report?.validation?.state === "validating" ||
  (report?.rows || []).some((row) => row.reason_code === "preflight_pending");

export const readinessKey = (envId, picks = {}, keys = null, trials = 1) => [
  "harness-readiness",
  envId,
  ...VERSION_PIECES.map((piece) => picks?.[piece] || null),
  keys ? [...keys].sort().join(",") : null,
  trials,
];

// The whole suite is a GET; a selection goes as a POST, which takes up to
// 1,000 scenarios where the GET's repeated keys stop at 200.
export const getReadiness = async (
  envId,
  picks,
  { keys = null, trials = 1 } = {},
) => {
  const fields = versionPickFields(picks);
  if (!keys) {
    return (await axios.get(capabilitiesPath(envId), { params: fields })).data;
  }
  return (
    await axios.post(capabilitiesPath(envId), {
      scenario_ids: keys,
      trials,
      ...fields,
    })
  ).data;
};

export function useReadiness(
  envId,
  picks,
  { keys = null, trials = 1, enabled = true } = {},
) {
  return useQuery({
    queryKey: readinessKey(envId, picks, keys, trials),
    queryFn: () => getReadiness(envId, picks, { keys, trials }),
    enabled: Boolean(envId) && enabled,
    retry: retryServerFailures,
    refetchInterval: (query) =>
      stillChecking(query.state.data) ? CHECK_POLL_MS : false,
    meta: { errorHandled: true },
  });
}

export const recheckReadiness = async (envId, picks) =>
  (await axios.post(recheckPath(envId), versionPickFields(picks))).data;

export const useRecheck = (envId) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (picks) => recheckReadiness(envId, picks),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ["harness-readiness", envId] }),
    meta: { errorHandled: true },
  });
};

// The first page (50) of the scenarios in one bucket (eligible, blocked,
// stale, untested) with their names as the server gives them. `keys` narrows
// it to the scenarios a run would send; none means the whole suite.
export const getReadinessCases = async (envId, picks, bucket, keys = null) => {
  const params = { ...versionPickFields(picks), bucket };
  if (keys) params.keys = keys;
  const page = (
    await axios.get(casesPath(envId), {
      params,
      // Repeated `keys=a&keys=b`, as the route reads them.
      paramsSerializer: { indexes: null },
    })
  ).data;
  return page.items || [];
};

export function useReadinessCases(
  envId,
  { picks = {}, bucket, keys = null, enabled = true } = {},
) {
  return useQuery({
    queryKey: [...readinessKey(envId, picks, keys), "cases", bucket],
    queryFn: () => getReadinessCases(envId, picks, bucket, keys),
    enabled: Boolean(envId) && Boolean(bucket) && enabled,
    retry: retryServerFailures,
    meta: { errorHandled: true },
  });
}
