// Shared fixtures for the compare page tests: an environment and its runs in
// the shape `useEnvironmentRuns` returns (executionToRun + ordinal/label).

export const ENV = {
  id: "env-1",
  name: "Support line",
  platform: { runTestId: "rt-1" },
};

const run = (ordinal, overrides = {}) => ({
  id: `r${ordinal}`,
  executionId: `r${ordinal}`,
  ordinal,
  label: `Run ${ordinal}`,
  status: "failed",
  runState: "completed",
  startedAt: `2026-10-0${ordinal}T10:00:00.000Z`,
  finishedAt: `2026-10-0${ordinal}T10:05:00.000Z`,
  total: 12,
  passed: 6,
  failed: 6,
  trials: 1,
  scenarioIds: [],
  agentVersion: "v1",
  ...overrides,
});

// Newest first, as the executions API returns them.
export const RUNS = [
  run(3, {
    agentVersion: "v2",
    passed: 9,
    failed: 3,
    trials: 3,
    scenarioIds: ["refund", "address", "cancel"],
  }),
  run(2, {
    agentVersion: "v2",
    passed: 8,
    failed: 4,
    trials: 3,
    scenarioIds: ["refund", "address", "cancel"],
  }),
  run(1, {
    total: 10,
    passed: 6,
    failed: 4,
    trials: 1,
    scenarioIds: ["refund", "address"],
  }),
];

export const runsResult = (runs = RUNS) => ({
  runs,
  count: runs.length,
  coveredScenarioCount: null,
  isLoading: false,
});

export const environmentResult = (env = ENV) => ({
  env,
  source: "harness",
  bootstrapState: undefined,
  notFound: false,
  error: null,
  isLoading: false,
});

// One raw `results[]` row of the v3 calls list.
export const callRow = (id, key, outcome, overrides = {}) => ({
  id,
  source_scenario_key: key,
  scenario: key,
  goal: "Billing",
  persona: "Dana",
  trial_index: 1,
  outcome,
  duration_seconds: 30,
  tokens: 1000,
  csat: 4,
  turn_count: 8,
  latency_ms: 900,
  evaluations: [
    {
      id: "ev-task",
      name: "Task done",
      score: outcome === "passed" ? 0.9 : 0.2,
      passed: outcome === "passed",
    },
  ],
  sub_goal_results: [],
  ...overrides,
});

export const EVAL_COLUMNS = [{ id: "ev-task", name: "Task done" }];

// A calls-list page as `fetchRunCalls` resolves it.
export const callsPage = (results, { count, totalPages = 1 } = {}) => ({
  results,
  count: count ?? results.length,
  total_pages: totalPages,
  evaluation_columns: EVAL_COLUMNS,
});

// Run 1 (baseline) against Run 3: refund was fixed, address regressed, greet
// passed in both, and cancel only ran in Run 3.
export const CALLS = {
  r1: [
    callRow("c1-refund", "refund", "failed"),
    callRow("c1-address", "address", "passed", { goal: "Account" }),
    callRow("c1-greet", "greet", "passed", { goal: "Account" }),
  ],
  r3: [
    callRow("c3-refund-2", "refund", "passed", { trial_index: 2 }),
    callRow("c3-refund-1", "refund", "passed", { duration_seconds: 20 }),
    callRow("c3-address", "address", "failed", { goal: "Account" }),
    callRow("c3-greet", "greet", "passed", { goal: "Account" }),
    callRow("c3-cancel", "cancel", "passed"),
  ],
};

// Resolves each run's calls from CALLS, one page each.
export const fetchCallsFrom =
  (calls = CALLS) =>
  (executionId) =>
    Promise.resolve(callsPage(calls[executionId] || []));
