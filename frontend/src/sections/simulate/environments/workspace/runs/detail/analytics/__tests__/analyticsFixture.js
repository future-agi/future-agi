// Run analytics payloads shaped like run_results_v3 `build_run_analytics`,
// one per agent type. The backend names the noun in its metric labels
// ("Total calls" / "Total chats"), and a chat run has no voice-only metrics.
const metric = (key, label, value, unit = "number", measured = 4) => ({
  key,
  label,
  value,
  unit,
  measured,
  total: 6,
  note: "",
});

const seriesRow = (label, extra) => ({
  label,
  calls: 1,
  started_at: null,
  latency_ms: null,
  duration_ms: null,
  llm_cents: null,
  tts_cents: null,
  stt_cents: null,
  storage_cents: null,
  ...extra,
});

function base(noun) {
  const Noun = noun === "chat" ? "Chat" : "Call";
  return {
    execution: { id: "execution-1", name: "Refunds" },
    summary: {
      total: 6,
      measured: 5,
      pass_rate: 60,
      evaluators: 1,
      outcomes: { passed: 3, failed: 2, error: 0, inconclusive: 1 },
      duration: { average: 210, p50: 189, measured: 6, total: 6 },
      latency: { average: 450, measured: 5, total: 6 },
      tokens: { total_value: 1000, measured: 2, total: 6 },
      cost_cents: { total_value: 250, average: 41.7, measured: 6, total: 6 },
    },
    reliability: { trials: 1, scenarios: 0, rows: [] },
    evaluations: [
      {
        id: "eval-1",
        name: "Policy adherence",
        passed: 3,
        failed: 2,
        measured: 5,
        errored: 1,
        missing: 0,
        pass_rate: 60,
      },
    ],
    dashboard: {
      metrics: [
        metric("pass_rate", `${Noun}s passed`, 60, "percent", 5),
        metric("total", `Total ${noun}s`, 6, "number", 6),
        metric("connected_rate", `${Noun}s connected (%)`, 83.33, "percent"),
        metric("drop_off", "Drop-off", 0, "percent"),
        metric("csat", "Avg CSAT (0–10)", 7.4, "number", 2),
        metric(
          "agent_latency",
          noun === "chat" ? "Agent response time" : "Agent latency",
          450,
          "ms",
          5,
        ),
        metric("duration", `Avg ${noun} duration`, 210, "seconds", 6),
        metric("turns", `Avg turns/${noun}`, 9.1, "number", 6),
        metric("duration_p90", `${Noun} duration p90`, 308100, "ms", 6),
        metric("cost_per_pass", "Cost / pass", 83.3, "cents", 6),
        metric("total_cost", "Total cost", 250, "cents", 6),
      ],
      run_health: {
        show_banner: false,
        attempted: 6,
        ran_cleanly: 6,
        connected: 5,
        errored: 0,
        not_evaluated: 1,
        eval_errors: 0,
      },
      comparison: {
        available: true,
        previous_execution_id: "execution-0",
        shared_scenarios: 2,
        newly_passing: ["Handle a refund"],
        newly_failing: ["Cancel an order"],
      },
      csat: {
        measured: 2,
        total: 6,
        satisfied: 1,
        satisfied_percent: 50,
        bins: [
          { label: "4", lower: 3.5, upper: 4.5, count: 1, danger: true },
          { label: "9", lower: 8.5, upper: 9.5, count: 1, danger: false },
        ],
        agreement: { compared: 2, agreed: 1, percent: 50 },
      },
      agent_response_time: {
        measured: 5,
        total: 6,
        target_ms: 1500,
        p50: 400,
        p95: 900,
        at_or_above_target: 0,
        at_or_above_target_percent: 0,
        bins: [
          { label: "400ms", lower: 400, upper: 425, count: 5, danger: false },
        ],
      },
      pipeline_cost: [
        { key: "llm", label: "LLM", total_cents: 250, share: 100 },
        { key: "tts", label: "TTS", total_cents: null, share: null },
        { key: "stt", label: "STT", total_cents: null, share: null },
        { key: "storage", label: "Storage", total_cents: null, share: null },
      ],
      evaluation_summary: {
        graders: 1,
        passed: 3,
        measured: 5,
        pass_rate: 60,
        errored_checks: 0,
      },
      use_case_risk: [
        {
          scenario: "Handle a refund",
          queued: 0,
          in_progress: 0,
          passed: 2,
          failed: 1,
          error: 0,
          inconclusive: 1,
        },
      ],
      goal_count: 1,
      breakdowns: [
        {
          key: "goal_outcome",
          label: "Goal outcome breakdown",
          total: 6,
          headline: { label: "passed", count: 3, share: 50 },
          segments: [
            { label: "passed", count: 3, share: 50, statuses: ["passed"] },
            { label: "failed", count: 2, share: 33.3, statuses: ["failed"] },
            {
              label: "inconclusive",
              count: 1,
              share: 16.7,
              statuses: ["inconclusive"],
            },
          ],
        },
        {
          key: "disconnection",
          label: noun === "chat" ? "How chats ended" : "How calls ended",
          total: 6,
          segments: [
            { label: "Caller hung up", count: 4, share: 66.7 },
            { label: "Simulator ended", count: 2, share: 33.3 },
          ],
        },
        {
          key: "sentiment",
          label: "Provider sentiment",
          total: 6,
          segments: [
            { label: "positive", count: 5, share: 83.3 },
            { label: "negative", count: 1, share: 16.7 },
          ],
        },
      ],
      voice_slos: [
        {
          key: "model",
          label: "LLM response",
          measured: 3,
          p50: 900,
          p90: 1200,
          p99: 1400,
        },
      ],
      interruptions: { total: 2, measured: 2, average: 1 },
      series: [
        seriesRow("1", { latency_ms: 420, duration_ms: 189000, llm_cents: 40 }),
        seriesRow("2", { latency_ms: null, duration_ms: null }),
        seriesRow("3", { latency_ms: 480, duration_ms: 308100, llm_cents: 60 }),
      ],
      series_mode: "calls",
      agent_latency_percentiles: [
        { percentile: 50, value: 400 },
        { percentile: 90, value: 470 },
        { percentile: 99, value: 480 },
      ],
      latency_percentiles: [
        { percentile: 50, value: 189000 },
        { percentile: 90, value: 308100 },
        { percentile: 99, value: 338520 },
      ],
      distributions: [
        {
          key: "latency_ms",
          measured: 5,
          average: 450,
          max: 480,
          p50: 400,
          p90: 470,
          p99: 480,
        },
      ],
      tools: {
        total_invocations: 3,
        total_tools: 1,
        volume: [
          {
            name: "lookup_order",
            invocations: 3,
            measured: 3,
            failures: 1,
            failure_rate: 33.33,
            failure_label: "33% · 1/3",
          },
        ],
        failures: [
          {
            name: "lookup_order",
            invocations: 3,
            measured: 3,
            failures: 1,
            failure_rate: 33.33,
            failure_label: "33% · 1/3",
          },
        ],
      },
      slowest_tasks: [
        {
          id: "call-1",
          rank: 1,
          label: "Handle a refund",
          axis_label: "#1 Handle a refund",
          value: 308,
          modality: noun === "chat" ? "text" : "voice",
          provider: "vapi",
        },
      ],
      most_expensive_tasks: [],
      unavailable_features: [
        {
          key: "failure_attribution",
          reason:
            "Error Feed failure domains and retry policies are not recorded on simulation calls.",
        },
        {
          key: "transport_cost",
          reason:
            "No distinct transport cost component is recorded; storage cost is shown separately.",
        },
      ],
    },
  };
}

export function voiceAnalytics() {
  const data = base("call");
  data.dashboard.metrics.push(
    metric("wpm", "Agent WPM", 167),
    metric("stop", "Agent stop latency", 289, "ms"),
    metric("talk", "Agent share of talk time", 48, "percent"),
  );
  return data;
}

export function chatAnalytics() {
  return base("chat");
}

export const AGENT_TYPES = [
  ["voice", voiceAnalytics],
  ["chat", chatAnalytics],
];
