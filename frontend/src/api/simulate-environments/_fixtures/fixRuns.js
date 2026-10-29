// Fix my agent v0 reads (GET /simulate/api/agent-fix-runs/{id}/) in the
// contract's exact shape, standing in for the backend until it lands. Field
// names are the contract's, verbatim; every state and agent kind the fix
// page has to render is here once.

const BASE_VERSION_ID = "agv-8";

const tiers = (overrides = {}) => [
  { name: "static", status: "passed", detail: "1 file changed, 6 lines" },
  {
    name: "target",
    status: "passed",
    detail: "1 of 1 target scenarios passed",
  },
  {
    name: "held-back",
    status: "passed",
    detail: "2 of 2 held-back scenarios passed",
  },
  {
    name: "must-pass",
    status: "passed",
    detail: "9 of 9 must-pass scenarios held",
  },
  { name: "repeats", status: "passed", detail: "3 repeats agree" },
  ...Object.entries(overrides).map(([name, rest]) => ({ name, ...rest })),
];

const candidate = (fixId, attempt, outcome, extra = {}) => ({
  id: `${fixId}-c${attempt}`,
  attempt,
  agent_version_id: `agv-${8 + attempt}`,
  version_number: 8 + attempt,
  summary:
    attempt % 2
      ? "Escalate refused late cancellations to the front desk."
      : "Say when lookup_order applies before answering.",
  outcome,
  verdict: {
    accepted: outcome === "winner" || outcome === "finalist",
    reason:
      outcome === "rejected"
        ? "target: 0 of 1 target scenarios passed"
        : "proven on targets at the acceptance bar; must-pass held",
    target_pass_rate: outcome === "rejected" ? [0.0, 0.0] : [0.33, 1.0],
    held_back_pass_rate: outcome === "rejected" ? null : [0.17, 0.83],
    must_pass_regressions: [],
    must_pass_within_noise: [],
    tiers:
      outcome === "rejected"
        ? [
            {
              name: "static",
              status: "passed",
              detail: "1 file changed, 6 lines",
            },
            {
              name: "target",
              status: "failed",
              detail: "0 of 1 target scenarios passed",
            },
            {
              name: "must-pass",
              status: "skipped",
              detail: "not run: an earlier tier failed",
            },
          ]
        : tiers(),
  },
  check_runs: [
    {
      run_id: `rt-${fixId}`,
      test_execution_id: `te-${fixId}-c${attempt}`,
      label: `c${attempt}-target`,
      state: outcome === "pending" ? "running" : "completed",
    },
  ],
  pull_request_url: "",
  created_at: `2026-10-29T08:${String(10 + attempt).padStart(2, "0")}:05Z`,
  ...extra,
});

const fix = (id, order, overrides = {}) => ({
  id,
  number: order,
  environment_id: "env-fixture",
  environment: {
    name: "new env",
    version: 1,
    default_agent_version_id: BASE_VERSION_ID,
  },
  source_run_id: "te-source-run-1",
  source_run_number: 1,
  base_agent_version_id: BASE_VERSION_ID,
  agent: {
    name: "Pickup dispatch agent",
    kind: "archive",
    version_number: 8,
    ref: null,
  },
  state: "ready",
  step: "done",
  stage: "Ship",
  request: {
    target_scenario_keys: ["late-cancel"],
    protected_scenario_keys: ["on-time-cancel"],
    held_back_scenario_keys: ["late-cancel-2", "late-cancel-3"],
    failing_sub_goals: ["escalated"],
    surfaces: ["prompt"],
    locked_paths: [],
    budget_credits: 1500,
    max_minutes: 150,
  },
  spent_credits: 412,
  committed_credits: 520,
  plan: {
    target: 6,
    held_back: 2,
    must_pass: 9,
    protected: 1,
    max_candidates: 4,
    repeats: 3,
    baseline_spread_pts: 17,
  },
  diagnosis: {
    causes: ["The instructions never ask the agent to escalate cancellations"],
  },
  report: null,
  failure: null,
  check_runs: [
    {
      run_id: `rt-${id}`,
      test_execution_id: `te-${id}-baseline`,
      label: "baseline",
      state: "completed",
    },
  ],
  candidates: [],
  started_at: `2026-10-${String(10 + order).padStart(2, "0")}T08:06:01Z`,
  finished_at: `2026-10-${String(10 + order).padStart(2, "0")}T08:40:00Z`,
  created_at: `2026-10-${String(10 + order).padStart(2, "0")}T08:06:00Z`,
  ...overrides,
});

const running = { finished_at: null, report: null };

const finishedCandidates = (id) => [
  candidate(id, 1, "rejected"),
  candidate(id, 2, "finalist"),
  candidate(id, 3, "winner", {
    verdict: {
      ...candidate(id, 3, "winner").verdict,
      must_pass_regressions: ["on-time-cancel"],
      must_pass_within_noise: ["refund-window"],
    },
  }),
];

export const FIX_RUN_FIXTURES = [
  fix("fix-queued", 15, {
    ...running,
    state: "queued",
    step: "reproduce",
    stage: "Starting",
    plan: null,
    diagnosis: null,
    started_at: null,
    spent_credits: 0,
    committed_credits: 0,
    check_runs: [],
  }),
  fix("fix-reproduce", 14, {
    ...running,
    state: "running",
    step: "reproduce",
    stage: "Reproduce",
    diagnosis: null,
  }),
  fix("fix-search", 13, {
    ...running,
    state: "running",
    step: "search",
    stage: "Screen · attempt 2",
    candidates: [
      candidate("fix-search", 1, "rejected"),
      candidate("fix-search", 2, "pending", {
        verdict: {
          accepted: false,
          reason: "",
          target_pass_rate: [0.33, 0.67],
          held_back_pass_rate: null,
          must_pass_regressions: [],
          must_pass_within_noise: [],
          tiers: [
            {
              name: "static",
              status: "passed",
              detail: "1 file changed, 4 lines",
            },
            { name: "target", status: "pending", detail: "" },
          ],
        },
      }),
    ],
  }),
  fix("fix-verify", 12, {
    ...running,
    state: "running",
    step: "verify",
    stage: "Verify · attempt 3",
    candidates: finishedCandidates("fix-verify").map((c) =>
      c.outcome === "winner" ? { ...c, outcome: "pending" } : c,
    ),
  }),
  fix("fix-stopping", 11, {
    ...running,
    state: "stopping",
    step: "search",
    stage: "Propose · attempt 2",
    candidates: [candidate("fix-stopping", 1, "rejected")],
  }),
  fix("fix-ready-archive", 10, {
    report: {
      ending: "ready",
      summary: "Found a fix that holds on scenarios it never saw.",
    },
    candidates: finishedCandidates("fix-ready-archive"),
  }),
  fix("fix-ready-github", 9, {
    agent: {
      name: "Pickup dispatch agent",
      kind: "github",
      version_number: 8,
      ref: "main @ 0487e25",
    },
    request: {
      ...fix("x", 0).request,
      surfaces: ["prompt", "code"],
      locked_paths: ["spec.json"],
    },
    report: { ending: "ready", summary: "Found a fix." },
    candidates: finishedCandidates("fix-ready-github"),
  }),
  fix("fix-ready-no-held", 8, {
    request: { ...fix("x", 0).request, held_back_scenario_keys: [] },
    plan: { ...fix("x", 0).plan, target: 3, held_back: 0 },
    report: { ending: "ready", summary: "Proven on the targets only." },
    candidates: finishedCandidates("fix-ready-no-held").map((c) => ({
      ...c,
      verdict: {
        ...c.verdict,
        held_back_pass_rate: null,
        must_pass_regressions: [],
        tiers: c.verdict.tiers.filter((t) => t.name !== "held-back"),
      },
    })),
  }),
  fix("fix-ready-shipped", 7, {
    environment: {
      name: "new env",
      version: 1,
      default_agent_version_id: "agv-11",
    },
    report: { ending: "ready", summary: "Found a fix." },
    candidates: finishedCandidates("fix-ready-shipped"),
  }),
  fix("fix-no-improvement", 6, {
    state: "no_improvement",
    report: {
      ending: "no_improvement",
      summary:
        "No safe improvement: every candidate broke a must-pass scenario.",
    },
    candidates: [
      candidate("fix-no-improvement", 1, "rejected"),
      candidate("fix-no-improvement", 2, "rejected"),
    ],
  }),
  fix("fix-not-reproduced", 5, {
    state: "not_reproduced",
    plan: null,
    report: {
      ending: "not_reproduced",
      summary: "The failure did not happen again on the current version.",
    },
  }),
  fix("fix-out-of-budget", 4, {
    state: "out_of_budget",
    spent_credits: 1500,
    committed_credits: 1500,
    report: {
      ending: "out_of_budget",
      summary: "Stopped at the 1,500 credit limit before a candidate held.",
    },
    candidates: [candidate("fix-out-of-budget", 1, "rejected")],
  }),
  fix("fix-stopped", 3, {
    state: "stopped",
    report: null,
    candidates: [candidate("fix-stopped", 1, "rejected")],
  }),
  fix("fix-failed", 2, {
    state: "failed",
    plan: null,
    report: { ending: "failed", summary: "" },
    failure: {
      code: "engine_error",
      message: "The engine could not start the agent's sandbox.",
    },
  }),
  fix("fix-unknown-identity", 1, {
    ...running,
    number: null,
    source_run_number: null,
    environment: {
      name: "new env",
      version: null,
      default_agent_version_id: BASE_VERSION_ID,
    },
    state: "running",
    step: "search",
    stage: "Diagnose",
    plan: null,
    diagnosis: null,
  }),
];

const clone = (value) => JSON.parse(JSON.stringify(value));

let store = clone(FIX_RUN_FIXTURES);

export function resetFixRunFixtures() {
  store = clone(FIX_RUN_FIXTURES);
}

export function fixtureFixRuns(envId) {
  return store
    .map((f) => ({ ...clone(f), environment_id: envId }))
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export function fixtureFixRun(fixId) {
  const found = store.find((f) => f.id === fixId);
  return found ? clone(found) : undefined;
}

export function updateFixtureFixRun(fixId, update) {
  store = store.map((f) => (f.id === fixId ? update(clone(f)) : f));
  return fixtureFixRun(fixId);
}

export const FIXTURE_PULL_REQUEST_URL =
  "https://github.com/acme/dispatch-agent/pull/42";

export function fixturePatch(candidateId) {
  return [
    "diff --git a/instructions.md b/instructions.md",
    "--- a/instructions.md",
    "+++ b/instructions.md",
    "@@ -12,6 +12,8 @@ When the caller asks to cancel",
    " Confirm the booking reference before changing anything.",
    "-Cancel the booking and confirm the refund.",
    "+If the pickup is less than 2 hours away, do not cancel it yourself.",
    "+Escalate the cancellation to the front desk and say you have done so.",
    " Read the refund policy back in one sentence.",
    `+<!-- ${candidateId} -->`,
    "",
  ].join("\n");
}

// Per-scenario results of each check run (GET /simulate/v3/test-executions/
// {id}/calls/ mapped by mapCallRow), for the Per scenario tab.
const SCENARIOS = [
  { key: "late-cancel", name: "Late cancellation" },
  { key: "late-cancel-2", name: "Late cancellation, repeat caller" },
  { key: "late-cancel-3", name: "Late cancellation, angry caller" },
  { key: "on-time-cancel", name: "Cancellation with notice" },
  { key: "refund-window", name: "Refund window" },
];

const BASELINE = {
  "late-cancel": "failed",
  "late-cancel-2": "failed",
  "late-cancel-3": "failed",
  "on-time-cancel": "passed",
  "refund-window": "passed",
};

const BY_ATTEMPT = {
  1: BASELINE,
  2: { ...BASELINE, "late-cancel": "passed" },
  3: {
    ...BASELINE,
    "late-cancel": "passed",
    "late-cancel-2": "passed",
    "on-time-cancel": "failed",
  },
};

export function fixtureRunTasks(testExecutionId) {
  const attempt = Number(/-c(\d+)$/.exec(testExecutionId || "")?.[1]);
  const statuses = BY_ATTEMPT[attempt] || BASELINE;
  return SCENARIOS.flatMap((s) =>
    [1, 2, 3].map((trial) => ({
      id: `${testExecutionId}-${s.key}-${trial}`,
      sourceScenarioKey: s.key,
      sourceScenario: s.name,
      trialIndex: trial,
      status: statuses[s.key],
    })),
  );
}
