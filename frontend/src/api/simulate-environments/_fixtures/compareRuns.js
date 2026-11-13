// A `GET …/harness-environments/{id}/compare-runs/` response in the frozen
// contract: three runs (the first is the baseline), two scenario rows, grouped
// by use case. Each `calls` item is a `/calls/` `results` item (RunCall).
// `compareRunsContract.test.js` holds it to the generated response schema.

const uuid = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

export const RUN = { a: uuid(1), b: uuid(2), c: uuid(3) };
export const CALL = {
  aRefund1: uuid(11),
  aRefund2: uuid(12),
  bRefund1: uuid(21),
  bRefund2: uuid(22),
  aEsc1: uuid(13),
  bEsc1: uuid(23),
  cEsc1: uuid(33),
};

export const COMPARE_EVAL_COLUMNS = [
  { id: "eval-1", name: "Refund correctness", kind: "evaluation" },
  { id: "eval-2", name: "Tone", kind: "evaluation" },
];

// A summary metric as the server sends it.
const stat = (average) => ({
  average,
  p50: average,
  p75: average,
  p90: average,
  p95: average,
  p99: average,
  measured: 4,
  total: 4,
});

const summary = (passed, failed, extra = {}) => ({
  total: passed + failed,
  outcomes: {
    queued: 0,
    in_progress: 0,
    passed,
    failed,
    error: 0,
    inconclusive: 0,
  },
  measured: passed + failed,
  pass_rate: passed + failed ? (passed / (passed + failed)) * 100 : null,
  duration: stat(60),
  latency: stat(400),
  tokens: { ...stat(2000), total_value: 8000 },
  cost_cents: { ...stat(2), total_value: 8 },
  ...extra,
});

const scoring = {
  not_applicable: 0,
  pending: 0,
  succeeded: 4,
  failed: 0,
  timed_out: 0,
};

const statusCounts = (passed, failed) => ({
  queued: 0,
  in_progress: 0,
  passed,
  failed,
  inconclusive: 0,
  error: 0,
});

const execution = (id, ordinal, agent, env, wholeRun) => ({
  id,
  run_test_id: uuid(100),
  name: `Run ${ordinal}`,
  ordinal,
  status: "completed",
  agent_version: agent,
  pinned_agent_version: {
    id: uuid(200 + Number(agent.slice(1))),
    label: agent,
  },
  environment_version: { id: uuid(300 + Number(env.slice(1))), label: env },
  suite_version: { id: uuid(400), label: "v1" },
  agent_type: "voice",
  trials: 2,
  started_at: "2026-11-01T10:00:00Z",
  completed_at: "2026-11-01T10:30:00Z",
  resume: { resumable: false, finished: 4, unfinished: 0, history: [] },
  summary: wholeRun,
});

const REFUND = {
  goal: "Refund a double charge",
  scenario: "Routine refund",
  source_scenario_key: "refund-double-charge",
  scenario_details: "Customer requests a refund for a duplicate charge.",
  ideal_outcome: "Refund is created after account verification",
  persona: "Impatient caller",
  persona_details: {
    name: "Impatient caller",
    voice: "US female",
    age: "34",
    traits: ["impatient"],
  },
};

const ESCALATE = {
  goal: "Escalate to a human",
  scenario: "Angry escalation",
  source_scenario_key: "escalate-angry",
  scenario_details: "Customer demands a supervisor.",
  ideal_outcome: "Agent hands over to a human",
  persona: "Angry caller",
  persona_details: {
    name: "Angry caller",
    voice: "UK male",
    age: "52",
    traits: [],
  },
};

// The deprecated name list the server still sends beside sub_goal_results.
const withSubGoals = (call) => ({
  ...call,
  sub_goals: call.sub_goal_results.map((goal) => goal.name),
});

// One RunCall, as `/calls/` sends it.
export const compareCall = (id, base, fields = {}) =>
  withSubGoals({
    id,
    outcome: "passed",
    execution_status: "completed",
    harness_outcome_status: "passed",
    started_at: "2026-11-01T10:00:00Z",
    completed_at: "2026-11-01T10:01:00Z",
    cost_cents: 2,
    cost_breakdown_cents: {
      stt: 0.5,
      llm: 1,
      tts: 0.5,
      storage: 0,
      customer: 2,
    },
    ended_reason: "customer-ended-call",
    error_message: "",
    scoring_status: "succeeded",
    csat_status: "succeeded",
    csat_reason: null,
    failure_domain: null,
    failure_code: null,
    modality: "voice",
    provider: "vapi",
    csat: 8,
    turn_count: 10,
    latency_ms: 400,
    avg_stop_time_after_interruption: 600,
    ai_interruption_count: 1,
    tokens: 2000,
    duration_seconds: 60,
    trial_index: 1,
    conversation_branch: "main",
    sub_goal_results: [
      { name: "identity_verified", passed: true },
      { name: "refund_created", passed: true },
    ],
    evaluations: [
      {
        id: "eval-1",
        name: "Refund correctness",
        type: "Pass/Fail",
        value: "Passed",
        score: 1,
        passed: true,
        reason: "",
        status: "succeeded",
      },
      {
        id: "eval-2",
        name: "Tone",
        type: "score",
        value: 0.9,
        score: 0.9,
        passed: null,
        reason: "",
        status: "succeeded",
      },
    ],
    ...base,
    ...fields,
  });

const failedRefund = {
  outcome: "failed",
  harness_outcome_status: "failed",
  latency_ms: 550,
  tokens: 2600,
  duration_seconds: 75,
  sub_goal_results: [
    { name: "identity_verified", passed: true },
    { name: "refund_created", passed: false },
  ],
  evaluations: [
    {
      id: "eval-1",
      name: "Refund correctness",
      type: "Pass/Fail",
      value: "Failed",
      score: 0,
      passed: false,
      reason: "",
      status: "succeeded",
    },
    {
      id: "eval-2",
      name: "Tone",
      type: "score",
      value: 0.6,
      score: 0.6,
      passed: null,
      reason: "",
      status: "succeeded",
    },
  ],
};

const group = (label, passed, failed) => ({
  key: label,
  label,
  result_ids: [],
  ...summary(passed, failed),
  aggregates: {
    csat: 8,
    turns: 10,
    latency_ms: 420,
    avg_stop_time_after_interruption: 600,
    ai_interruptions: 1,
    tokens: 4000,
    evaluations: {
      "eval-2": { scored: passed + failed, score_sum: 0.8 * (passed + failed) },
    },
  },
});

export const compareRunsResponse = () => ({
  baseline_execution_id: RUN.a,
  runs: [
    {
      execution: execution(RUN.a, 1, "v2", "v3", summary(1, 2)),
      is_baseline: true,
      summary: summary(1, 2, { scoring }),
      status_counts: statusCounts(1, 2),
      histograms: {
        "eval-1": [1, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        "eval-2": [0, 0, 0, 0, 0, 1, 1, 0, 0, 1],
      },
    },
    {
      execution: execution(RUN.b, 2, "v3", "v3", summary(3, 0)),
      is_baseline: false,
      summary: summary(3, 0, { scoring }),
      status_counts: statusCounts(3, 0),
      histograms: {
        "eval-1": [0, 0, 0, 0, 0, 0, 0, 0, 0, 3],
        "eval-2": [0, 0, 0, 0, 0, 0, 0, 0, 1, 2],
      },
    },
    {
      execution: execution(RUN.c, 3, "v3", "v4", summary(1, 0)),
      is_baseline: false,
      summary: summary(1, 0, { scoring }),
      status_counts: statusCounts(1, 0),
      histograms: {
        "eval-1": [0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        "eval-2": [0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
      },
    },
  ],
  evaluation_columns: COMPARE_EVAL_COLUMNS,
  facets: {
    goal: [
      { value: "Refund a double charge", count: 4 },
      { value: "Escalate to a human", count: 3 },
    ],
    sub_goal: [
      { value: "identity_verified", count: 7 },
      { value: "refund_created", count: 4 },
    ],
    status: [
      { value: "passed", count: 5 },
      { value: "failed", count: 2 },
    ],
    persona: [
      { value: "Elena Vargas", count: 4 },
      { value: "Arjun Mehta", count: 3 },
    ],
    age: [{ value: "36", count: 7 }],
    attack: [{ value: "Prompt injection", count: 7 }],
    task: [],
  },
  level_labels: {
    identity_verified: "Identity verified",
    refund_created: "Refund created",
    "Prompt injection": "Prompt injection",
  },
  changed_count: 2,
  count: 2,
  page: 1,
  page_size: 25,
  total_pages: 1,
  groups: [
    {
      key: "Refund a double charge",
      label: "Refund a double charge",
      row_ids: ["refund-double-charge:aaa"],
      // The group's rows on every page, not only this one.
      row_count: 3,
      runs: {
        [RUN.a]: group("Refund a double charge", 1, 1),
        [RUN.b]: group("Refund a double charge", 2, 0),
      },
    },
    {
      key: "Escalate to a human",
      label: "Escalate to a human",
      row_ids: ["escalate-angry:bbb"],
      row_count: 1,
      runs: {
        [RUN.a]: group("Escalate to a human", 0, 1),
        [RUN.b]: group("Escalate to a human", 1, 0),
        [RUN.c]: group("Escalate to a human", 1, 0),
      },
    },
  ],
  rows: [
    {
      row_id: "refund-double-charge:aaa",
      scenario_key: "refund-double-charge",
      scenario: REFUND.scenario,
      use_case: REFUND.goal,
      persona: REFUND.persona,
      persona_details: REFUND.persona_details,
      situation: REFUND.scenario_details,
      ideal_outcome: REFUND.ideal_outcome,
      calls: {
        [RUN.a]: [
          compareCall(CALL.aRefund1, REFUND, { trial_index: 1 }),
          compareCall(CALL.aRefund2, REFUND, {
            trial_index: 2,
            ...failedRefund,
          }),
        ],
        [RUN.b]: [
          compareCall(CALL.bRefund1, REFUND, {
            trial_index: 1,
            latency_ms: 350,
          }),
          compareCall(CALL.bRefund2, REFUND, { trial_index: 2, tokens: 1800 }),
        ],
        [RUN.c]: [],
      },
      movement: "improved",
    },
    {
      row_id: "escalate-angry:bbb",
      scenario_key: "escalate-angry",
      scenario: ESCALATE.scenario,
      use_case: ESCALATE.goal,
      persona: ESCALATE.persona,
      persona_details: ESCALATE.persona_details,
      situation: ESCALATE.scenario_details,
      ideal_outcome: ESCALATE.ideal_outcome,
      calls: {
        [RUN.a]: [compareCall(CALL.aEsc1, ESCALATE, failedRefund)],
        [RUN.b]: [compareCall(CALL.bEsc1, ESCALATE)],
        [RUN.c]: [compareCall(CALL.cEsc1, ESCALATE)],
      },
      movement: "improved",
    },
  ],
});
