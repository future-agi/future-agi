import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { CAPABILITY, useFeatureAllowed } from "src/hooks/useCapabilities";
import axios, { endpoints } from "src/utils/axios";
import {
  FIX_LIVE_STATES,
  FIX_POLL_MS,
  useFixRun,
} from "src/api/simulate-environments/fixRuns";

// Run Epoch from an Error Feed issue (agent-fix-issues): the plan, the job that
// drafts the issue's scenarios and measures the current version, and the epoch it
// launches. One job per attempt; the issue reads its latest.

// Pre-launch phases poll: the builder drafts, then the current version runs.
const LIVE_PHASES = new Set(["drafting", "measuring"]);

export const issueEpochPlanKey = (clusterId, environmentId) => [
  "agent-fix-issue-plan",
  clusterId,
  environmentId ?? null,
];
export const issueEpochJobKey = (jobId) => ["agent-fix-issue", jobId];
export const issueEpochsKey = (clusterIds) => [
  "agent-fix-issues",
  [...clusterIds].sort(),
];

// The job as the issue page reads it (issueEpochView): its phase before launch, the
// ending when it stopped before an epoch, and the epoch once one started.
export function issueEpochStatus(job, epoch = null) {
  if (!job) return null;
  return {
    jobId: job.id,
    environmentId: job.environment_id,
    importId: job.evidence_import,
    phase: job.phase,
    ending: job.ending ?? null,
    fixRunId: job.fix_run ?? null,
    epoch,
  };
}

// The plan as the drawer reads it: the project's remembered environment, the code-agent
// environments it can run on (or why none can), the sampled traces with spares to swap
// in, and the launch form's defaults and limits.
export function issuePlanOf(plan) {
  if (!plan) return null;
  return {
    environment: plan.environment ?? null,
    environments: plan.environments ?? [],
    noFit: plan.blocked ?? null,
    picks: plan.sample?.picks ?? [],
    alternates: plan.sample?.alternates ?? [],
    defaults: plan.defaults ?? null,
    limits: plan.limits ?? null,
  };
}

const isLiveJob = (job) =>
  !!job &&
  (LIVE_PHASES.has(job.phase) ||
    (job.fix_run && FIX_LIVE_STATES.has(job.fix_run_state)));

export async function getIssueEpochPlan(clusterId, environmentId) {
  const res = await axios.get(endpoints.agentFixIssues.plan(), {
    params: {
      cluster_id: clusterId,
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

// The plan makes no model call, but it samples the issue's traces: it is read when
// the person opens Run Epoch, never on page load.
export function useIssueEpochPlan(clusterId, { environmentId, enabled }) {
  return useQuery({
    queryKey: issueEpochPlanKey(clusterId, environmentId),
    queryFn: () => getIssueEpochPlan(clusterId, environmentId),
    enabled: enabled && !!clusterId,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
    meta: { errorHandled: true },
  });
}

// The latest job of each issue on the page, keyed by cluster id. Asks nothing when
// the org lacks Epoch, since the API answers 402 there.
export function useIssueEpochs(clusterIds) {
  const { allowed } = useFeatureAllowed(CAPABILITY.FIX_MY_AGENT);
  const ids = clusterIds.filter(Boolean);
  const { data } = useQuery({
    queryKey: issueEpochsKey(ids),
    queryFn: () => listIssueEpochs(ids),
    enabled: allowed && ids.length > 0,
    refetchInterval: (query) =>
      Object.values(query.state.data?.results ?? {}).some(isLiveJob)
        ? FIX_POLL_MS
        : false,
    meta: { errorHandled: true },
  });
  return data?.results ?? {};
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

// One issue's epoch, as the issue page shows it: its latest job, and the epoch's
// own read once the job launched one.
export function useIssueEpoch(clusterId, { enabled = true } = {}) {
  const jobs = useIssueEpochs(enabled && clusterId ? [clusterId] : []);
  const latest = jobs[clusterId] ?? null;
  const { data: job } = useIssueEpochJob(latest?.id);
  const current = job ?? latest;
  const { fix } = useFixRun(current?.fix_run ?? null);
  return issueEpochStatus(current, fix ?? null);
}

const refreshIssue = (queryClient, job) => {
  queryClient.invalidateQueries({ queryKey: ["agent-fix-issues"] });
  if (job?.id) {
    queryClient.setQueryData(issueEpochJobKey(job.id), job);
  }
};

export function useCreateIssueEpoch() {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: createIssueEpoch,
    onSuccess: (job) => refreshIssue(queryClient, job),
  });
}

export function useRunIssueEpoch(jobId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: (body) => runIssueEpoch(jobId, body),
    onSuccess: (job) => refreshIssue(queryClient, job),
  });
}

export function useStopIssueEpoch(jobId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: () => stopIssueEpoch(jobId),
    onSuccess: (job) => refreshIssue(queryClient, job),
  });
}
