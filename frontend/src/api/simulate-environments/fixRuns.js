import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { CAPABILITY, useFeatureAllowed } from "src/hooks/useCapabilities";
import axios, { endpoints } from "src/utils/axios";

import { fetchRunCalls, mapCallRow } from "./runCalls";

// Fix my agent v0 (agent-fix-runs).
export const FIX_LIVE_STATES = new Set(["queued", "running", "stopping"]);
export const FIX_POLL_MS = 5000;

export const FIX_RUNS_PAGE_SIZE = 25;

// The page, size and states sit after the env id, so invalidating
// fixRunsKey(envId) refreshes every page and filter of that environment.
export const fixRunsKey = (envId, page, pageSize, states = []) =>
  page == null
    ? ["agent-fix-runs", envId]
    : ["agent-fix-runs", envId, page, pageSize, states.join(",")];
export const fixRunKey = (fixId) => ["agent-fix-run", fixId];

// A finished fix is read again while its winner's evals grade: it ships once they have.
export const fixPollInterval = (fix) =>
  fix && (FIX_LIVE_STATES.has(fix.state) || fix.eval_grading === "pending")
    ? FIX_POLL_MS
    : false;

// One server page, 1-based, as { results, count }; with states, only the fixes
// in them, counted alone.
export async function listFixRuns(
  envId,
  { page = 1, limit = FIX_RUNS_PAGE_SIZE, states = [] } = {},
) {
  const params = { environment_id: envId, page, limit };
  if (states.length) params.state = states.join(",");
  const res = await axios.get(endpoints.agentFixRuns.list(), { params });
  return res.data;
}

export async function getFixRun(fixId) {
  const res = await axios.get(endpoints.agentFixRuns.detail(fixId));
  return res.data;
}

// The app's axios rejects with the body spread plus statusCode; an
// axios-style { response } is accepted too.
export const isNotFound = (error) =>
  (error?.response?.status ?? error?.statusCode) === 404;

// 0-based page in, like the other environment lists; count is the server's
// total across pages, of the given states when there are some. Polls while a
// fix on the visible page is live. Asks nothing when the org lacks Fix my
// agent, since the list answers 402 there.
export function useEnvironmentFixRuns(
  envId,
  { page = 0, pageSize = FIX_RUNS_PAGE_SIZE, states = [] } = {},
) {
  const { allowed, isLoading: capabilityLoading } = useFeatureAllowed(
    CAPABILITY.FIX_MY_AGENT,
  );
  const key = fixRunsKey(envId, page, pageSize, states);
  const { data, isLoading, error } = useQuery({
    queryKey: key,
    queryFn: () =>
      listFixRuns(envId, { page: page + 1, limit: pageSize, states }),
    enabled: allowed && !!envId,
    // The list says why it failed, and steps back from a page past the end
    // at once: no toast, and no retry of a 404.
    meta: { errorHandled: true },
    retry: (failures, failure) => !isNotFound(failure) && failures < 1,
    // Another page keeps the last one on screen while it loads; another
    // environment or filter never shows the last one's fixes.
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[1] === key[1] &&
      previousQuery?.queryKey[4] === key[4]
        ? previous
        : undefined,
    refetchInterval: (query) =>
      (query.state.data?.results || []).some((f) =>
        FIX_LIVE_STATES.has(f.state),
      )
        ? FIX_POLL_MS
        : false,
  });
  const fixes = data?.results ?? [];
  return {
    fixes,
    count: data?.count ?? 0,
    isLoading: capabilityLoading || isLoading,
    // A page past the end answers 404: the list's page can step back.
    error,
  };
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

// A failed fix started again: a new fix with the same request, which reuses the
// check runs the failed one finished.
export async function retryFixRun(fixId) {
  const res = await axios.post(endpoints.agentFixRuns.retry(fixId), {});
  return res.data;
}

export function useRetryFixRun(fix) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: () => retryFixRun(fix.id),
    onSuccess: () => refreshFix(queryClient, fix),
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

// The baseline's and one candidate's check-run results, for the Scenarios
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
  // Each call keeps the check run it came from, so it can link to its run.
  const rowsOf = (list, from) =>
    list.flatMap((r, i) =>
      (r.data ?? []).map((task) => ({ ...task, checkRun: from[i] })),
    );
  return {
    before: rowsOf(results.slice(0, baseline.length), baseline),
    after: rowsOf(results.slice(baseline.length), candidateRuns),
    run: candidateRuns[0],
    isLoading: results.some((r) => r.isLoading),
    isError: results.some((r) => r.isError),
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
// The failing scenarios of a run that an evidence import added: Run Epoch starts a fix on
// them, from the evidence they came from.
export const importedFailingKeys = (options) =>
  (options?.scenarios ?? [])
    .filter((one) => one.evidence_import_id && one.failing_sub_goals?.length)
    .map((one) => one.key);

// Run Epoch's starting cases: the imported failing ones first, then the run's
// other failing cases, only as many as reach the launch minimum. The person can
// still deselect any of them, or add more.
export const epochTargets = (options) => {
  const imported = importedFailingKeys(options);
  const others = (options?.scenarios ?? [])
    .filter((one) => !one.evidence_import_id && one.failing_sub_goals?.length)
    .map((one) => one.key);
  const short = (options?.limits?.scenarios_min ?? 0) - imported.length;
  const added = others.slice(0, Math.max(short, 0));
  return { imported, added, keys: [...imported, ...added] };
};

// What Run Epoch put in the form, so nothing is added silently.
export const epochTargetsNote = ({ imported, added }) => {
  const plural = (n) => (n === 1 ? "" : "s");
  return added.length
    ? `${imported.length} from imported evidence, ${added.length} more failing scenario${plural(added.length)} from this run to reach the minimum`
    : `${imported.length} failing scenario${plural(imported.length)} from imported evidence`;
};

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
