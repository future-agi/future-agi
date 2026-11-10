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
      query.state.data?.validation?.state === "validating"
        ? CHECK_POLL_MS
        : false,
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

// Every scenario key in one bucket, page by page (50 a page), for a suite too
// big for the report's lists, which stop at 200.
export const getReadinessCases = async (envId, picks, bucket) => {
  const keys = [];
  let cursor = null;
  do {
    const params = { ...versionPickFields(picks), bucket };
    if (cursor) params.cursor = cursor;
    const page = (await axios.get(casesPath(envId), { params })).data;
    keys.push(...(page.items || []).map((item) => item.key));
    cursor = page.next || null;
  } while (cursor);
  return keys;
};

export function useReadinessCases(
  envId,
  { picks = {}, bucket, enabled = true } = {},
) {
  return useQuery({
    queryKey: [...readinessKey(envId, picks), "cases", bucket],
    queryFn: () => getReadinessCases(envId, picks, bucket),
    enabled: Boolean(envId) && Boolean(bucket) && enabled,
    retry: retryServerFailures,
    meta: { errorHandled: true },
  });
}
