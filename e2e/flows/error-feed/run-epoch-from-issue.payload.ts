// Stubbed responses for ERR-E2E-006, in the agent-fix-issues contract's shapes.
// No imports, so both suites can read it: the flow serves these from the browser,
// and frontend/src/api/errorFeed/__tests__/epochFromIssueStubs.test.js parses every
// one with the generated zod (frontend/src/generated/api-contracts/api.zod.ts), so
// a contract change fails the frontend suite instead of leaving these stale.

type Json = Record<string, unknown>;

// The prebuilt template env-voice-support: the workspace opens it without a built
// environment, so the epoch page renders (as SIM-E2E-007/008 use it). The contract's
// environment ids are UUIDs; the frontend parse swaps this one id, nothing else.
export const ENV_ID = 'env-voice-support';
const SOURCE_RUN_ID = '4d5e6f70-8192-4a3b-9c4d-5e6f708192a3';
const AGENT_VERSION_ID = '5e6f7081-92a3-4b4c-8d5e-6f708192a3b4';
const CANDIDATE_VERSION_ID = '6f708192-a3b4-4c5d-9e6f-708192a3b4c5';
export const ENV_NAME = 'Customer Support Line';
export const PROJECT_ID = '38338ba4-c1a4-46e8-be28-6fd4ba08e9d6';
export const CASE_KEPT = 'a1f0c3d2-4b5e-4f60-8a71-0b2c3d4e5f60';
export const CASE_DROPPED = 'b2e1d4c3-5a6f-4071-9b82-1c3d4e5f6071';
export const ISSUE_TITLE = 'Agent promises refunds outside the 30 day policy window';

export type IssuePhase = 'drafting' | 'review' | 'measuring' | 'launched';
// The issue's cluster id (at most 20 characters) and UUIDs for the job, the epoch
// and its winning candidate.
export type Ids = { cluster: string; job: string; epoch: string; candidate: string };

export const pick = (n: number, reason: 'example' | 'cited' | 'recent'): Json => ({
  member_id: `member-${n}`,
  unit: 'trace',
  source_id: `trace-${n}-0000000000`,
  reason,
  started_at: '2026-11-18T10:00:00Z',
});

// AgentFixIssueDefaultsSerializer at the platform's values.
export const DEFAULTS = {
  surfaces: [
    { id: 'prompt', label: 'Instructions', allowed: true, reason: null },
    { id: 'tools', label: 'Tools', allowed: true, reason: null },
    { id: 'code', label: 'Code', allowed: false, reason: 'Only a GitHub agent can change its code.' },
  ],
  budget_credits: 1500,
  budget_credits_max: 50000,
  max_minutes_max: 1440,
  reply_latency_margin_pct: 25,
  cost_margin_pct: 25,
};

const issueRef = (clusterId: string): Json => ({
  cluster_id: clusterId,
  project_id: PROJECT_ID,
  title: ISSUE_TITLE,
});

// GET agent-fix-issues/plan/: the first time, no environment is remembered.
export function issuePlan(clusterId: string, withEnvironment: boolean): Json {
  return {
    issue: issueRef(clusterId),
    environment: withEnvironment ? { id: ENV_ID, name: ENV_NAME } : null,
    blocked: null,
    sample: { unit: 'trace', picks: [pick(1, 'example'), pick(2, 'cited')], alternates: [pick(3, 'recent')] },
    open_job_id: null,
    issue_scenarios: { held: 4, limit: 36 },
    defaults: DEFAULTS,
  };
}

const evidenceCase = (id: string, firstLine: string): Json => ({
  id,
  source_id: `trace-${id.slice(0, 4)}`,
  name: '',
  status: 'drafted',
  reason: '',
  source_available: true,
  scenario_key: `issue-${id.slice(0, 6)}`,
  scenario_name: firstLine,
  new_checks: [],
  changed_since_import: false,
  also_from: 0,
  duplicate_of: null,
  trace_ids: [`trace-${id.slice(0, 4)}`],
  started_at: '2026-11-18T10:00:00Z',
  ended_at: '2026-11-18T10:01:00Z',
  transcript: {
    status: 'present',
    reason: '',
    turns: [{ role: 'user', text: firstLine, truncated: false, trace_id: 't', at: null }],
  },
  tool_calls: { status: 'missing', reason: '', calls: [] },
  tool_inputs: { status: 'missing', reason: '' },
  tool_outputs: { status: 'missing', reason: '' },
  starting_state: { status: 'missing', reason: '' },
  labels: { status: 'missing', reason: '', labels: [] },
  expected_outcome: { status: 'missing', reason: '', text: '' },
});

const IMPORT_ID = 'c3f2e5d4-6b70-4182-8c93-2d4e5f607182';
const ENV_UUID_FOR_IMPORT = '2c4e6f80-a1b2-4c3d-8e4f-5a6b7c8d9e0f';

// The job's summary, as the batch status lists it and as the detail starts.
export function issueJob(ids: Ids, phase: IssuePhase): Json {
  return {
    id: ids.job,
    phase,
    issue: issueRef(ids.cluster),
    environment: { id: ENV_ID, name: ENV_NAME },
    sample: [pick(3, 'recent'), pick(2, 'cited')],
    ending: null,
    baseline: null,
    epoch:
      phase === 'launched'
        ? {
            id: ids.epoch,
            number: 3,
            environment_id: ENV_ID,
            state: 'ready',
            step: 'done',
            failure_code: null,
            retryable: false,
            limited_validation: false,
            pull_request_url: null,
          }
        : null,
    created_at: '2026-11-19T09:00:00Z',
  };
}

// GET agent-fix-issues/<id>/: the summary, the import it drafted and the launch defaults.
export function issueJobDetail(ids: Ids, phase: IssuePhase): Json {
  return {
    ...issueJob(ids, phase),
    defaults: DEFAULTS,
    evidence_import: {
      id: IMPORT_ID,
      environment_id: ENV_UUID_FOR_IMPORT,
      project_id: PROJECT_ID,
      dataset_id: null,
      agent_version_id: null,
      source_kind: 'trace',
      created_at: '2026-11-19T09:00:00Z',
      simulated: false,
      production: true,
      drafting: phase === 'drafting',
      cases:
        phase === 'drafting'
          ? []
          : [
              evidenceCase(CASE_KEPT, 'Can you move me to the 6pm flight instead?'),
              evidenceCase(CASE_DROPPED, 'Rebook me on anything before noon tomorrow.'),
            ],
      redaction: { method: 'presidio', reason: '', findings: [], total: 0 },
    },
  };
}

// GET agent-fix-issues/?cluster_ids=: the page's issues that have a job.
export function issueStatusList(ids: Ids, phase: IssuePhase | null): Json {
  return {
    results: phase ? [{ cluster_id: ids.cluster, project_id: PROJECT_ID, job: issueJob(ids, phase) }] : [],
  };
}

// GET agent-fix-runs/<id>/: a GitHub agent's epoch, Ready for review, whose winner has
// no pull request yet, carrying the issue it was run from.
export function readyEpoch(ids: Ids): Json {
  return {
    id: ids.epoch,
    number: 3,
    environment_id: ENV_ID,
    environment: { name: ENV_NAME, version: 1, default_agent_version_id: AGENT_VERSION_ID },
    source_run_id: SOURCE_RUN_ID,
    source_run_number: 6,
    base_agent_version_id: AGENT_VERSION_ID,
    agent: { name: 'Support agent', kind: 'github', version_id: AGENT_VERSION_ID, version_number: 1, ref: 'main @ 0487e25' },
    state: 'ready',
    stage: 'Done',
    step: 'done',
    plan: { target: 8, held_back: 4, must_pass: 3, protected: 0, max_candidates: 4, repeats: 3, baseline_spread_pts: 12 },
    diagnosis: { causes: ['It confirms before it reads the tool result'] },
    request: {
      target_scenario_keys: ['issue-a1f0c3'],
      failing_sub_goals: ['confirmed'],
      surfaces: ['prompt'],
      locked_paths: [],
      budget_credits: 1500,
      max_minutes: 150,
    },
    spent_credits: 640,
    committed_credits: 640,
    report: { ending: 'ready', summary: 'Ready for review.', winner_candidate_id: ids.candidate },
    failure: null,
    retried_from_id: null,
    retryable: false,
    limited_validation: false,
    issue: { job_id: ids.job, cluster_id: ids.cluster, project_id: PROJECT_ID, title: ISSUE_TITLE },
    from_issue: true,
    check_runs: [],
    candidates: [
      {
        id: ids.candidate,
        attempt: 1,
        agent_version_id: CANDIDATE_VERSION_ID,
        version_number: 2,
        summary: 'Read the tool result before confirming.',
        outcome: 'winner',
        verdict: {
          accepted: true,
          reason: 'passed every check',
          target_pass_rate: [0.25, 0.88],
          held_back_pass_rate: [0.25, 0.75],
          must_pass_regressions: [],
          must_pass_within_noise: [],
          tiers: [],
        },
        check_runs: [],
        pull_request_url: '',
      },
    ],
  };
}

// The request bodies the drawer must send for this plan and review.
export const createBody = (clusterId: string): Json => ({
  cluster_id: clusterId,
  project_id: PROJECT_ID,
  environment_id: ENV_ID,
  member_ids: ['member-3', 'member-2'],
});

export const RUN_BODY: Json = {
  case_ids: [CASE_KEPT],
  surfaces: ['prompt', 'tools'],
  locked_paths: [],
  budget_credits: 1500,
  reply_latency_margin_pct: 25,
  cost_margin_pct: 25,
};
