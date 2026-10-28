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
