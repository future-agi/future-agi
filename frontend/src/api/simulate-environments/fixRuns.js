import {
  keepPreviousData,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import axios, { endpoints } from "src/utils/axios";

import { fetchRunCalls, mapCallRow } from "./runCalls";

// Fix my agent v0 (agent-fix-runs).
export const FIX_LIVE_STATES = new Set(["queued", "running", "stopping"]);
export const FIX_POLL_MS = 5000;

export const FIX_RUNS_PAGE_SIZE = 25;

// The page and size sit after the env id, so invalidating fixRunsKey(envId)
// refreshes every page of that environment.
export const fixRunsKey = (envId, page, pageSize) =>
  page == null ? ["agent-fix-runs", envId] : ["agent-fix-runs", envId, page, pageSize];
export const fixRunKey = (fixId) => ["agent-fix-run", fixId];

export const fixPollInterval = (fix) =>
  fix && FIX_LIVE_STATES.has(fix.state) ? FIX_POLL_MS : false;

// One server page, 1-based, as { results, count }.
export async function listFixRuns(envId, { page = 1, limit = FIX_RUNS_PAGE_SIZE } = {}) {
  const res = await axios.get(endpoints.agentFixRuns.list(), {
    params: { environment_id: envId, page, limit },
  });
  return res.data;
}

export async function getFixRun(fixId) {
  const res = await axios.get(endpoints.agentFixRuns.detail(fixId));
  return res.data;
}

// 0-based page in, like the other environment lists; count is the server's
// total across pages. Polls while a fix on the visible page is live.
export function useEnvironmentFixRuns(
  envId,
  { page = 0, pageSize = FIX_RUNS_PAGE_SIZE } = {},
) {
  const { data, isLoading } = useQuery({
    queryKey: fixRunsKey(envId, page, pageSize),
    queryFn: () => listFixRuns(envId, { page: page + 1, limit: pageSize }),
    enabled: !!envId,
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      (query.state.data?.results || []).some((f) =>
        FIX_LIVE_STATES.has(f.state),
      )
        ? FIX_POLL_MS
        : false,
  });
  const fixes = data?.results ?? [];
  return { fixes, count: data?.count ?? 0, isLoading };
}

export function useFixRun(fixId) {
  const { data, isLoading, error } = useQuery({
    queryKey: fixRunKey(fixId),
    queryFn: () => getFixRun(fixId),
    enabled: !!fixId,
    refetchInterval: (query) => fixPollInterval(query.state.data),
  });
  return { fix: data, isLoading, error };
}

const PATCH_OFFER_CODES = new Set([
  "fix_github_not_connected",
  "fix_github_commit_unknown",
  "github_repository_invalid",
]);

// The app's axios rejects with the body spread plus statusCode; an
// axios-style { response } is accepted too.
export function fixErrorOf(error) {
  const data = error?.response?.data ?? error ?? {};
  const status = error?.response?.status ?? error?.statusCode;
  if (status === 402) {
    const entitlement = data.upgrade_required === true;
    return {
      code: entitlement ? "entitlement" : "usage",
      message:
        (typeof data.error === "object" && data.error?.message) ||
        data.message ||
        "",
      offerPatch: false,
    };
  }
  const code = typeof data.error === "string" ? data.error : data.code;
  return {
    code: code || "unknown",
    message: data.message || "",
    offerPatch: PATCH_OFFER_CODES.has(code),
  };
}

export const patchFileName = (fixId, attempt) =>
  `fix-${fixId}-attempt-${attempt}.patch`;

export async function stopFixRun(fixId) {
  const res = await axios.post(endpoints.agentFixRuns.stop(fixId), {});
  return res.data;
}

export async function shipCandidate(fix, candidateId) {
  if (fix.agent?.kind === "github") {
    const res = await axios.post(
      endpoints.agentFixRuns.pullRequest(fix.id, candidateId),
      {},
    );
    return { kind: "pull_request", url: res.data?.pull_request_url };
  }
  const res = await axios.post(
    endpoints.agentFixRuns.makeDefault(fix.id, candidateId),
    {},
  );
  return {
    kind:
      res.data?.outcome === "already_default" ? "already_default" : "activated",
  };
}

export async function fetchCandidatePatch(fixId, candidateId) {
  const res = await axios.get(
    endpoints.agentFixRuns.patch(fixId, candidateId),
    { responseType: "text" },
  );
  return res.data;
}

const refreshFix = (queryClient, fix) => {
  queryClient.invalidateQueries({ queryKey: fixRunKey(fix.id) });
  queryClient.invalidateQueries({ queryKey: fixRunsKey(fix.environment_id) });
};

export function useStopFixRun(fixId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: () => stopFixRun(fixId),
    onSuccess: (fix) => fix && refreshFix(queryClient, fix),
  });
}

export function useShipCandidate(fix) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (candidateId) => shipCandidate(fix, candidateId),
    onSuccess: () => refreshFix(queryClient, fix),
  });
}

const CALLS_PAGE_SIZE = 200;

// Reads pages until the run's count is reached; a check run can hold more
// calls than one page.
export async function collectAllCalls(fetchPage, pageSize = CALLS_PAGE_SIZE) {
  const rows = [];
  for (let page = 1; ; page += 1) {
    // eslint-disable-next-line no-await-in-loop
    const data = await fetchPage(page, pageSize);
    const results = data?.results ?? [];
    rows.push(...results);
    if (results.length < pageSize || rows.length >= (data?.count ?? 0)) {
      return rows;
    }
  }
}

// One check run's per-call results, mapped the way the run page maps them.
export async function fetchCheckRunTasks(testExecutionId) {
  const rows = await collectAllCalls((page, pageSize) =>
    fetchRunCalls(testExecutionId, { page, page_size: pageSize }),
  );
  return rows.map((row) => mapCallRow(row));
}

// The fix's own check runs are the agent before the change; labels are for
// display only, so every one of them counts as the baseline.
export function checkRunsForScenarios(fix, candidateId) {
  const candidate = (fix.candidates || []).find((c) => c.id === candidateId);
  return {
    before: fix.check_runs || [],
    after: candidate?.check_runs || [],
  };
}

// The baseline's and one candidate's check-run results, for the Per scenario
// tab: what the scenario did before the change and after it.
export function useFixScenarioRows(fix, candidateId) {
  const { before: baseline, after: candidateRuns } = checkRunsForScenarios(
    fix,
    candidateId,
  );
  const runs = [...baseline, ...candidateRuns];
  const results = useQueries({
    queries: runs.map((run) => ({
      queryKey: ["agent-fix-run-check-tasks", run.test_execution_id],
      queryFn: () => fetchCheckRunTasks(run.test_execution_id),
      enabled: !!run.test_execution_id,
      staleTime: 1000 * 60 * 5,
    })),
  });
  const rowsOf = (list) => list.flatMap((r) => r.data ?? []);
  return {
    before: rowsOf(results.slice(0, baseline.length)),
    after: rowsOf(results.slice(baseline.length)),
    run: candidateRuns[0],
    isLoading: results.some((r) => r.isLoading),
  };
}

export const fixLaunchOptionsKey = (sourceRunId) => [
  "agent-fix-launch-options",
  sourceRunId,
];

export async function getLaunchOptions(sourceRunId) {
  const res = await axios.get(endpoints.agentFixRuns.launchOptions(), {
    params: { source_run_id: sourceRunId },
  });
  return res.data;
}

// Everything the launch dialog shows and prefills, for one finished run.
export function useFixLaunchOptions(sourceRunId, { enabled = true } = {}) {
  const { data, isLoading, error } = useQuery({
    queryKey: fixLaunchOptionsKey(sourceRunId),
    queryFn: () => getLaunchOptions(sourceRunId),
    enabled: enabled && !!sourceRunId,
  });
  return { options: data, isLoading, error };
}

export async function launchFix(body) {
  const res = await axios.post(endpoints.agentFixRuns.list(), body);
  return res.data;
}

export function useLaunchFix(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (body) => launchFix(body),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: fixRunsKey(envId) }),
  });
}
