import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import {
  environmentVersionsKey,
  retryServerFailures,
  runPlanKey,
  versionPickFields,
} from "./versions";

// Run readiness for an agent, environment and scenario version together (the pair): what can run, what is held
// back and why. Worked out on every read; Re-check starts a new check of the pair, read again while it runs.

const CHECK_POLL_MS = 15000;
// The cases route reads at most this many scenario keys.
export const MAX_CASE_KEYS = 200;

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

// A pair check is running, or the agent's own start check (after a retry).
const stillChecking = (report) =>
  report?.validation?.state === "validating" ||
  (report?.rows || []).some((row) => row.reason_code === "preflight_pending");

// Under the run plan's key, so saves and default moves read it again too.
const readinessKey = (envId, picks, keys, trials) => [
  ...runPlanKey(envId, picks),
  "readiness",
  keys ?? null,
  trials,
];

// The whole suite is a GET; a named selection goes in a POST body, so any number of keys fits.
export const getReadiness = async (envId, picks, keys = null, trials = 1) => {
  const fields = versionPickFields(picks);
  const { data } = keys
    ? await axios.post(capabilitiesPath(envId), {
        ...fields,
        scenario_ids: keys,
        trials,
      })
    : await axios.get(capabilitiesPath(envId), { params: fields });
  return data;
};

export function useReadiness(
  envId,
  picks,
  { keys = null, trials = 1, enabled = true } = {},
) {
  return useQuery({
    queryKey: readinessKey(envId, picks, keys, trials),
    queryFn: () => getReadiness(envId, picks, keys, trials),
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
      queryClient.invalidateQueries({
        queryKey: [...environmentVersionsKey(envId), "run-plan"],
      }),
    meta: { errorHandled: true },
  });
};

// The first page (50) of the selected scenarios, each with the rows that hold it back (`by`), narrowed to one row
// when given. `keys` is the selection a run would send; none means the whole suite.
export const getReadinessCases = async (envId, picks, { keys, row }) => {
  const params = { ...versionPickFields(picks) };
  if (row) params.row = row;
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
  { picks = {}, keys = null, row = null, report = null, enabled = true } = {},
) {
  return useQuery({
    // `report` (when the report was worked out) ties the names to the counts beside them.
    queryKey: [...runPlanKey(envId, picks), "cases", keys, row, report],
    queryFn: () => getReadinessCases(envId, picks, { keys, row }),
    enabled: Boolean(envId) && enabled && (keys?.length ?? 0) <= MAX_CASE_KEYS,
    retry: retryServerFailures,
    meta: { errorHandled: true },
  });
}
