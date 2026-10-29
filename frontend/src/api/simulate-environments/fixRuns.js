import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import axios, { endpoints } from "src/utils/axios";

import { fetchRunCalls, mapCallRow } from "./runCalls";

import {
  FIXTURE_PULL_REQUEST_URL,
  fixtureFixRun,
  fixtureFixRuns,
  fixtureLaunch,
  fixtureLaunchOptions,
  fixturePatch,
  fixtureRunTasks,
  updateFixtureFixRun,
} from "./_fixtures/fixRuns";

// Fix my agent v0 (agent-fix-runs). Until the backend lands every read and
// write answers from contract-shaped fixtures; flip this to "api" to go live.
export const FIX_RUNS_SOURCE = "fixtures";

export const FIX_LIVE_STATES = new Set(["queued", "running", "stopping"]);
export const FIX_POLL_MS = 5000;

export const fixRunsKey = (envId) => ["agent-fix-runs", envId];
export const fixRunKey = (fixId) => ["agent-fix-run", fixId];

export const fixPollInterval = (fix) =>
  fix && FIX_LIVE_STATES.has(fix.state) ? FIX_POLL_MS : false;

const fromFixtures = () => FIX_RUNS_SOURCE === "fixtures";

export async function listFixRuns(envId) {
  if (fromFixtures()) return fixtureFixRuns(envId);
  const res = await axios.get(endpoints.agentFixRuns.list(), {
    params: { environment_id: envId },
  });
  return Array.isArray(res.data) ? res.data : res.data?.results ?? [];
}

export async function getFixRun(fixId) {
  if (fromFixtures()) {
    const fix = fixtureFixRun(fixId);
    if (!fix) throw new Error("not_found");
    // A stop settles within ~10 s on the real engine: the read after the one
    // that showed "stopping" shows "stopped".
    if (fix.state === "stopping" && fix.stop_requested) {
      updateFixtureFixRun(fixId, (f) => ({
        ...f,
        state: "stopped",
        finished_at: new Date().toISOString(),
      }));
    }
    return fix;
  }
  const res = await axios.get(endpoints.agentFixRuns.detail(fixId));
  return res.data;
}

export function useEnvironmentFixRuns(envId) {
  const { data, isLoading } = useQuery({
    queryKey: fixRunsKey(envId),
    queryFn: () => listFixRuns(envId),
    enabled: !!envId,
    refetchInterval: (query) =>
      (query.state.data || []).some((f) => FIX_LIVE_STATES.has(f.state))
        ? FIX_POLL_MS
        : false,
  });
  const fixes = data ?? [];
  return { fixes, count: fixes.length, isLoading };
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
  if (fromFixtures()) {
    return updateFixtureFixRun(fixId, (f) => ({
      ...f,
      state: "stopping",
      stop_requested: true,
    }));
  }
  const res = await axios.post(endpoints.agentFixRuns.stop(fixId), {});
  return res.data;
}

export async function shipCandidate(fix, candidateId) {
  const candidate = fix.candidates.find((c) => c.id === candidateId);
  if (fix.agent?.kind === "github") {
    if (fromFixtures()) {
      updateFixtureFixRun(fix.id, (f) => ({
        ...f,
        candidates: f.candidates.map((c) =>
          c.id === candidateId
            ? { ...c, pull_request_url: FIXTURE_PULL_REQUEST_URL }
            : c,
        ),
      }));
      return { kind: "pull_request", url: FIXTURE_PULL_REQUEST_URL };
    }
    const res = await axios.post(
      endpoints.agentFixRuns.pullRequest(fix.id, candidateId),
      {},
    );
    return { kind: "pull_request", url: res.data?.pull_request_url };
  }
  if (fromFixtures()) {
    const before = fixtureFixRun(fix.id);
    if (
      before.environment.default_agent_version_id === candidate.agent_version_id
    ) {
      return { kind: "already_default" };
    }
    updateFixtureFixRun(fix.id, (f) => ({
      ...f,
      environment: {
        ...f.environment,
        default_agent_version_id: candidate.agent_version_id,
      },
    }));
    return { kind: "activated" };
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
  if (fromFixtures()) return fixturePatch(candidateId);
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
  if (fromFixtures()) return fixtureRunTasks(testExecutionId);
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
  if (fromFixtures()) return fixtureLaunchOptions(sourceRunId);
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

export async function launchFix(envId, body) {
  if (fromFixtures()) return fixtureLaunch(envId, body);
  const res = await axios.post(endpoints.agentFixRuns.list(), body);
  return res.data;
}

export function useLaunchFix(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (body) => launchFix(envId, body),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: fixRunsKey(envId) }),
  });
}
