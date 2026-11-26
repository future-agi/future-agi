// Stubbed responses for SIM-E2E-041, in the agent-fix-runs and v3 calls contracts'
// shapes. No imports, so both suites can read it: the flow serves these from the
// browser, and frontend/src/api/simulate-environments/__tests__/epochNamesStubs.test.js
// parses every one with the generated zod (frontend/src/generated/api-contracts/api.zod.ts),
// so a contract change fails the frontend suite instead of leaving these stale.

type Json = Record<string, unknown>;

// The prebuilt template env-voice-support: the workspace opens it without a built
// environment, so the epoch page renders (as SIM-E2E-007/008 and ERR-E2E-006 use it).
// The contract's environment ids are UUIDs; the frontend parse swaps this one id.
export const ENV_ID = 'env-voice-support';
const ENV_NAME = 'Customer Support Line';
const AGENT_VERSION_ID = '1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d';
const CANDIDATE_VERSION_ID = '2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e';
const RUN_TEST_ID = '3c4d5e6f-7a8b-4c9d-8e0f-2a3b4c5d6e7f';
const SOURCE_RUN_ID = '4d5e6f7a-8b9c-4d0e-9f1a-3b4c5d6e7f80';

// The server's change line for the winner's patch (wording.changes, counted once
// from the stored changed files), and the engine's own note on its last round,
// which the page shows only on hover.
export const CHANGES = 'Changes instructions.md: 115 lines added, 17 removed.';
export const ENGINE_NOTE = 'Ask for the order date before offering a refund.';

// Two scenarios the candidate called. The first has a stored name that differs from
// its key; the second has none, so the read's scenario_names leaves it out.
export const NAMED_KEY = 'refund-after-window';
export const NAMED = 'Refund asked for 40 days after delivery';
export const UNNAMED_KEY = 'cancel-mid-call';

// The epoch, its winner, and the check runs on each side: UUIDs the flow mints.
export type Ids = {
  epoch: string;
  candidate: string;
  baseRun: string;
  baseExecution: string;
  candidateRun: string;
  candidateExecution: string;
};

// GET agent-fix-runs/<id>/: a GitHub agent's epoch, Ready for review, whose winner
// fixed both scenarios. Its own check run is the agent before the change; the
// winner lists its own.
export function readyEpoch(ids: Ids): Json {
  return {
    id: ids.epoch,
    number: 4,
    environment_id: ENV_ID,
    environment: { name: ENV_NAME, version: 1, default_agent_version_id: AGENT_VERSION_ID },
    source_run_id: SOURCE_RUN_ID,
    source_run_number: 7,
    base_agent_version_id: AGENT_VERSION_ID,
    agent: { name: 'Support agent', kind: 'github', version_id: AGENT_VERSION_ID, version_number: 1, ref: 'main @ 4f1c2ab' },
    state: 'ready',
    stage: 'Done',
    step: 'done',
    plan: { target: 2, held_back: 2, must_pass: 0, protected: 0, max_candidates: 4, repeats: 3, baseline_spread_pts: 10 },
    diagnosis: { causes: ['It offers a refund before it checks the order date'] },
    request: {
      target_scenario_keys: [NAMED_KEY, UNNAMED_KEY],
      failing_sub_goals: ['checked_window'],
      surfaces: ['prompt'],
      locked_paths: [],
      budget_credits: 1500,
      max_minutes: 150,
    },
    spent_credits: 480,
    committed_credits: 480,
    report: { ending: 'ready', summary: 'Ready for review.', winner_candidate_id: ids.candidate },
    failure: null,
    retried_from_id: null,
    retryable: false,
    attempt_of_id: null,
    counted: null,
    limited_validation: false,
    issue: null,
    from_issue: false,
    flaky_protected_text: [],
    scenario_names: { [NAMED_KEY]: NAMED },
    check_runs: [{ run_id: ids.baseRun, test_execution_id: ids.baseExecution, label: 'Current version', state: 'completed' }],
    candidates: [
      {
        id: ids.candidate,
        attempt: 1,
        agent_version_id: CANDIDATE_VERSION_ID,
        version_number: 2,
        summary: ENGINE_NOTE,
        outcome: 'winner',
        verdict: {
          accepted: true,
          reason: 'passed every check',
          target_pass_rate: [0, 1],
          held_back_pass_rate: [0, 1],
          must_pass_regressions: [],
          must_pass_within_noise: [],
          tiers: [],
        },
        check_runs: [
          { run_id: ids.candidateRun, test_execution_id: ids.candidateExecution, label: 'Candidate 1', state: 'completed' },
        ],
        pull_request_url: '',
        wording: {
          headline: 'Target pass rate 0% → 100%. No must-pass scenario broke.',
          must_pass: 'No must-pass scenario broke.',
          must_pass_broken: 0,
          changes: CHANGES,
          caveats: [],
        },
      },
    ],
  };
}

const spread = (value: number): Json => ({
  average: value, p50: value, p75: value, p90: value, p95: value, p99: value, measured: 2, total: 2,
});
const outcomes = (passed: number, failed: number): Json => ({
  queued: 0, in_progress: 0, passed, failed, error: 0, cancelled: 0, inconclusive: 0, no_score: 0,
});

// One measured call on one scenario, the fields fetchRunCalls and mapCallRow
// (frontend/src/api/simulate-environments/runCalls.js) read.
function call(id: string, key: string, passed: boolean): Json {
  return {
    id,
    scenario: key,
    scenario_details: 'A customer asks for a refund.',
    goal: 'Handle the refund request',
    ideal_outcome: 'The agent checks the order date first.',
    conversation_branch: 'main',
    persona: 'Dana',
    persona_details: { name: 'Dana', voice: 'calm', age: '34', traits: ['direct'] },
    sub_goals: ['checked_window'],
    sub_goal_results: [{ name: 'checked_window', display_name: 'Checks the order date', passed }],
    harness_outcome_status: passed ? 'passed' : 'failed',
    source_scenario_key: key,
    trial_index: 1,
    outcome: passed ? 'passed' : 'failed',
    execution_status: 'completed',
    modality: 'text',
    provider: 'harness',
    started_at: '2026-11-25T10:00:00Z',
    completed_at: '2026-11-25T10:01:00Z',
    duration_seconds: 60,
    latency_ms: 900,
    avg_stop_time_after_interruption: null,
    ai_interruption_count: null,
    turn_count: 6,
    tokens: 1200,
    cost_cents: 4,
    cost_breakdown_cents: { stt: 0, llm: 4, tts: 0, storage: 0, customer: 0 },
    csat: 0,
    ended_reason: 'completed',
    error_message: '',
    evaluations: [],
    scoring_status: 'succeeded',
    csat_status: 'not_applicable',
    csat_reason: 'Not scored for text calls.',
    failure_domain: 'agent',
    failure_code: passed ? 'none' : 'sub_goal_failed',
  };
}

// GET /simulate/v3/test-executions/<id>/calls/: one call per scenario, failing
// before the change and passing with the candidate.
export function checkRunCalls(ids: Ids, executionId: string): Json {
  const passed = executionId === ids.candidateExecution;
  const results = [
    call(`${executionId.slice(0, 35)}1`, NAMED_KEY, passed),
    call(`${executionId.slice(0, 35)}2`, UNNAMED_KEY, passed),
  ];
  const summary = {
    total: 2,
    outcomes: outcomes(passed ? 2 : 0, passed ? 0 : 2),
    measured: 2,
    pass_rate: passed ? 100 : 0,
    duration: spread(60),
    latency: spread(900),
    tokens: { ...spread(1200), total_value: 2400 },
    cost_cents: { ...spread(4), total_value: 8 },
    scoring: { not_applicable: 0, pending: 0, succeeded: 2, failed: 0, timed_out: 0 },
  };
  return {
    execution: {
      id: executionId,
      run_test_id: RUN_TEST_ID,
      name: passed ? 'Candidate 1' : 'Current version',
      status: 'completed',
      started_at: '2026-11-25T10:00:00Z',
      completed_at: '2026-11-25T10:05:00Z',
      ordinal: passed ? 9 : 8,
      agent_version: passed ? 'v2' : 'v1',
      agent_type: 'text',
      resume: { resumable: false, finished: 2, unfinished: 0, history: [] },
      summary,
    },
    summary,
    count: 2,
    page: 1,
    page_size: 100,
    total_pages: 1,
    results,
    groups: [],
    facets: { goal: [], sub_goal: [], status: [] },
    status_counts: outcomes(passed ? 2 : 0, passed ? 0 : 2),
    evaluation_columns: [],
  };
}
