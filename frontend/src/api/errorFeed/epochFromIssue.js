import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { CAPABILITY, useFeatureAllowed } from "src/hooks/useCapabilities";
import axios, { endpoints } from "src/utils/axios";
import {
  FIX_LIVE_STATES,
  FIX_POLL_MS,
  useFixRun,
} from "src/api/simulate-environments/fixRuns";
import { listHarnessEnvironments } from "src/api/simulate-environments/harnessEnvironments";

// Run Epoch from an Error Feed issue (agent-fix-issues): the plan, the job that
// drafts the issue's scenarios and measures the current version, and the epoch it
// launches. One job per attempt; the issue reads its latest.

// The job's phase, derived by the server: before the epoch, launched, or ended.
const LIVE_PHASES = new Set(["drafting", "measuring"]);

const ISSUE_EPOCH_PLAN = "agent-fix-issue-plan";
export const issueEpochPlanKey = (projectId, clusterId, environmentId) => [
  ISSUE_EPOCH_PLAN,
  projectId,
  clusterId,
  environmentId ?? null,
];
export const issueEpochJobKey = (jobId) => ["agent-fix-issue", jobId];
const ISSUE_EPOCHS = "agent-fix-issues";
export const issueEpochsKey = (clusterIds) => [
  ISSUE_EPOCHS,
  [...clusterIds].sort(),
];
// A cluster id is unique within its project: the list matches on both.
export const issueKey = (projectId, clusterId) => `${projectId}:${clusterId}`;

// The job's epoch, as the job read summarises it, in the epoch read's own shape so
// the issue's view reads both alike. `summary` marks what the summary leaves out:
// the candidates, the failure's message and the agent.
const epochOfJob = (epoch) =>
  epoch
    ? {
        id: epoch.id,
        number: epoch.number,
        environment_id: epoch.environment_id,
        state: epoch.state,
        step: epoch.step,
        failure: epoch.failure_code ? { code: epoch.failure_code } : null,
        retryable: epoch.retryable,
        limited_validation: epoch.limited_validation,
        summary: true,
      }
    : null;

// The job as the issue page reads it (issueEpochView): its phase, the ending when it
// stopped before an epoch, and the epoch once one started. The epoch's own read
// replaces the job's summary of it when the page has it.
export function issueEpochStatus(job, epoch = null) {
  if (!job) return null;
  return {
    jobId: job.id,
    phase: job.phase,
    environmentId: job.environment?.id ?? null,
    ending: job.ending ?? null,
    evidenceImport: job.evidence_import ?? null,
    baseline: job.baseline ?? null,
    defaults: job.defaults ?? null,
    epoch: epoch ?? epochOfJob(job.epoch),
  };
}

const isLiveJob = (job) =>
  !!job &&
  (LIVE_PHASES.has(job.phase) ||
    (!!job.epoch && FIX_LIVE_STATES.has(job.epoch.state)));

export async function getIssueEpochPlan({
  clusterId,
  projectId,
  environmentId,
}) {
  const res = await axios.get(endpoints.agentFixIssues.plan(), {
    params: {
      cluster_id: clusterId,
      project_id: projectId,
      ...(environmentId ? { environment_id: environmentId } : {}),
    },
  });
  return res.data;
}

export async function listIssueEpochs(clusterIds) {
  const res = await axios.get(endpoints.agentFixIssues.list(), {
    params: { cluster_ids: clusterIds.join(",") },
  });
  return res.data;
}

export async function getIssueEpoch(jobId) {
  const res = await axios.get(endpoints.agentFixIssues.detail(jobId));
  return res.data;
}

export async function createIssueEpoch(body) {
  const res = await axios.post(endpoints.agentFixIssues.list(), body);
  return res.data;
}

export async function runIssueEpoch(jobId, body) {
  const res = await axios.post(endpoints.agentFixIssues.run(jobId), body);
  return res.data;
}

export async function stopIssueEpoch(jobId) {
  const res = await axios.post(endpoints.agentFixIssues.stop(jobId), {});
  return res.data;
}

// The plan makes no model call, but it reads the issue's traces to sample them: it is
// read when the person opens a new plan, never on page load. A blocked plan is read
// again when they come back to the tab, after fixing what blocked it; one that only
// waits for them to pick an environment is not.
export function useIssueEpochPlan(
  { clusterId, projectId, environmentId },
  { enabled },
) {
  return useQuery({
    queryKey: issueEpochPlanKey(projectId, clusterId, environmentId),
    queryFn: () => getIssueEpochPlan({ clusterId, projectId, environmentId }),
    enabled: enabled && !!clusterId && !!projectId,
    refetchOnWindowFocus: (query) => {
      const code = query.state.data?.blocked?.code;
      return !!code && code !== "environment_not_picked";
    },
    retry: false,
    meta: { errorHandled: true },
  });
}

// An epoch runs only on a code agent: one uploaded or from GitHub. The server says
// which environments those are (`epoch_pickable`), as the plan decides it; one not
// ready yet is listed too, and once picked the plan says what to do.
const PICKER_PAGE = 100;

export function useCodeAgentEnvironments({ enabled }) {
  return useQuery({
    queryKey: ["agent-fix-issue-environments"],
    queryFn: () => listHarnessEnvironments({ page: 1, limit: PICKER_PAGE }),
    select: (data) => (data?.results ?? []).filter((env) => env.epoch_pickable),
    enabled,
    meta: { errorHandled: true },
  });
}

// The latest job of each issue on the page, by issueKey, in one read whatever the
// projects. Asks nothing when the org lacks Epoch, since the API answers 402. A
// failed read is reported, so a page never offers Run Epoch over a job it can't see.
export function useIssueEpochs(clusterIds) {
  const { allowed } = useFeatureAllowed(CAPABILITY.FIX_MY_AGENT);
  const ids = [...new Set(clusterIds.filter(Boolean))];
  const { data, isError, isLoading } = useQuery({
    queryKey: issueEpochsKey(ids),
    queryFn: () => listIssueEpochs(ids),
    enabled: allowed && ids.length > 0,
    refetchInterval: (query) =>
      (query.state.data?.results ?? []).some((one) => isLiveJob(one.job))
        ? FIX_POLL_MS
        : false,
    meta: { errorHandled: true },
  });
  const jobs = {};
  for (const one of data?.results ?? []) {
    jobs[issueKey(one.project_id, one.cluster_id)] = one.job;
  }
  return { jobs, isError, isLoading };
}

export function useIssueEpochJob(jobId) {
  return useQuery({
    queryKey: issueEpochJobKey(jobId),
    queryFn: () => getIssueEpoch(jobId),
    enabled: !!jobId,
    refetchInterval: (query) =>
      isLiveJob(query.state.data) ? FIX_POLL_MS : false,
    meta: { errorHandled: true },
  });
}

// One issue's epoch, as the issue page shows it: its latest job, and the epoch's own
// read once the job launched one (Ship and Retry need the whole epoch). `known` is
// false until the issue's jobs were read, or when that read failed.
export function useIssueEpoch({ clusterId, projectId }) {
  const { jobs, isError, isLoading } = useIssueEpochs([clusterId]);
  const latest = jobs[issueKey(projectId, clusterId)] ?? null;
  const { data: job } = useIssueEpochJob(latest?.id);
  // The batch names the latest job; a detail read of an older one never wins.
  const current = job && job.id === latest?.id ? job : latest;
  const { fix } = useFixRun(current?.epoch?.id ?? null);
  return {
    status: issueEpochStatus(current, fix ?? null),
    known: !isError && !isLoading,
  };
}

// After any write, read the issue's jobs and plan again; a refusal can end the job
// too (a stale draft, a scenario gone), so this runs on success and on failure.
const refreshIssue = (queryClient, jobId) => {
  queryClient.invalidateQueries({ queryKey: [ISSUE_EPOCHS] });
  queryClient.invalidateQueries({ queryKey: [ISSUE_EPOCH_PLAN] });
  if (jobId) {
    queryClient.invalidateQueries({ queryKey: issueEpochJobKey(jobId) });
  }
};

const settleWith = (queryClient, jobIdOf) => ({
  onSuccess: (job) => {
    if (job?.id) queryClient.setQueryData(issueEpochJobKey(job.id), job);
  },
  onSettled: (job) => refreshIssue(queryClient, job?.id ?? jobIdOf()),
});

export function useCreateIssueEpoch() {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: createIssueEpoch,
    ...settleWith(queryClient, () => null),
  });
}

export function useRunIssueEpoch(jobId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (body) => runIssueEpoch(jobId, body),
    ...settleWith(queryClient, () => jobId),
  });
}

export function useStopIssueEpoch(jobId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: () => stopIssueEpoch(jobId),
    ...settleWith(queryClient, () => jobId),
  });
}
