import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cloneElement } from "react";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import { CHART_GUIDE } from "../analytics/chartGuide";

const useRunAnalytics = vi.fn();
vi.mock("recharts", async (importOriginal) => ({
  ...(await importOriginal()),
  ResponsiveContainer: ({ children }) =>
    cloneElement(children, { width: 600, height: 250 }),
}));
vi.mock("src/api/simulate-environments/runAnalytics", () => ({
  useRunAnalytics: (...args) => useRunAnalytics(...args),
}));

const { default: RunAnalytics } = await import("../RunAnalytics");

const analytics = {
  dashboard: {
    metrics: [
      {
        key: "pass_rate",
        label: "Calls passed",
        value: 66.67,
        unit: "percent",
        measured: 3,
        total: 4,
        note: "Passed every eval",
      },
      {
        key: "drop_off",
        label: "Drop-off",
        value: 25,
        unit: "percent",
        measured: 4,
        total: 4,
        note: "Caller hung up before passing",
      },
      {
        key: "cost_per_pass",
        label: "Cost / pass",
        value: 125,
        unit: "cents",
        measured: 4,
        total: 4,
        note: "Spend divided by passing calls",
      },
    ],
    breakdowns: [
      {
        key: "goal_outcome",
        total: 5,
        headline: { label: "passed", count: 2, share: 40 },
        segments: [
          { label: "passed", count: 2, share: 40, statuses: ["passed"] },
          { label: "failed", count: 1, share: 20, statuses: ["failed"] },
          { label: "escalated", count: 1, share: 20, statuses: ["escalated"] },
          {
            label: "inconclusive",
            count: 1,
            share: 20,
            statuses: ["inconclusive"],
          },
        ],
      },
      {
        key: "disconnection",
        total: 4,
        segments: [{ label: "Caller hung up", count: 4, share: 100 }],
      },
    ],
    evaluation_summary: {
      graders: 1,
      passed: 2,
      measured: 3,
      pass_rate: 66.67,
      errored_checks: 0,
    },
    voice_slos: [
      {
        key: "model",
        label: "LLM response",
        measured: 0,
        p50: null,
        p90: null,
        p99: null,
      },
    ],
    interruptions: { total: null, average: null, measured: 0 },
    series: [],
    agent_latency_percentiles: [],
    distributions: [],
    csat: {
      measured: 2,
      total: 4,
      satisfied: 1,
      satisfied_percent: 50,
      bins: [
        { label: "4", lower: 3.5, upper: 4.5, count: 1, danger: true },
        { label: "9", lower: 8.5, upper: 9.5, count: 1, danger: false },
      ],
      agreement: { compared: 2, agreed: 1, percent: 50 },
    },
    agent_response_time: {
      measured: 2,
      total: 4,
      target_ms: 1500,
      p50: 1500,
      p95: 1590,
      at_or_above_target: 1,
      at_or_above_target_percent: 50,
      bins: [
        { label: "1500ms", lower: 1500, upper: 1525, count: 1, danger: true },
      ],
    },
    pipeline_cost: [],
    tools: { total_invocations: 0, total_tools: 0, volume: [], failures: [] },
    use_case_risk: [
      {
        scenario: "Handle a refund",
        passed: 2,
        failed: 1,
        error: 0,
        inconclusive: 1,
      },
    ],
    goal_count: 1,
    slowest_tasks: [
      {
        id: "call-1",
        rank: 1,
        label: "Handle a refund",
        axis_label: "#1 Handle a refund",
        value: 30,
        modality: "voice",
        provider: "livekit",
      },
    ],
    most_expensive_tasks: [],
    run_health: {
      show_banner: true,
      attempted: 4,
      ran_cleanly: 4,
      connected: 4,
      errored: 0,
      not_evaluated: 1,
      eval_errors: 0,
    },
    comparison: {
      available: true,
      previous_execution_id: "execution-0",
      shared_scenarios: 2,
      newly_passing: ["refund-stable"],
      newly_failing: ["refund-flips"],
    },
  },
  summary: {
    total: 4,
    measured: 3,
    pass_rate: 66.67,
    evaluators: 1,
    outcomes: { passed: 2, failed: 1, error: 0, inconclusive: 1 },
    duration: { p50: 12, p95: 20, measured: 4, total: 4 },
    latency: { p50: 250, p95: 400, measured: 3, total: 4 },
    tokens: { total_value: 1000, measured: 2, total: 4 },
    cost_cents: { total_value: 250, average: 62.5, measured: 4, total: 4 },
  },
  scenario_risk: [
    {
      scenario: "Handle a refund",
      total: 4,
      pass_rate: 66.67,
      outcomes: { failed: 1, error: 0 },
    },
  ],
  reliability: {
    trials: 2,
    scenarios: 2,
    consistent_pass: 1,
    passed_at_least_once: 2,
    repeated: 2,
    flaky: 1,
    flip_rate: 50,
    pass_rate_interval: {
      low: 12.5,
      high: 95.1,
      effective_n: 3,
      evaluated: 3,
      clusters: 2,
    },
    rows: [
      {
        scenario: "refund-flips",
        runs: 2,
        passed: 1,
        failed: 1,
        error: 0,
        inconclusive: 0,
        evaluated: 2,
        pass_rate: 50,
        verdict: "flaky",
      },
      {
        scenario: "refund-stable",
        runs: 2,
        passed: 1,
        failed: 0,
        error: 0,
        inconclusive: 1,
        evaluated: 1,
        pass_rate: 100,
        verdict: "passed",
      },
    ],
  },
  turn_distribution: [
    { turn_count: 4, passed: 2, failed: 1, error: 0, inconclusive: 1 },
  ],
  evaluations: [
    {
      id: "eval-1",
      name: "Policy adherence",
      passed: 2,
      failed: 1,
      measured: 3,
      errored: 1,
      missing: 0,
      pass_rate: 66.67,
    },
  ],
  failure_breakdown: [
    { reason: "Evaluation: Policy adherence", failures: 1, share: 100 },
  ],
  provider_breakdown: [
    {
      provider: "vapi",
      total: 4,
      pass_rate: 66.67,
      outcomes: { failed: 1, error: 0 },
    },
  ],
  modality_breakdown: [
    {
      modality: "voice",
      total: 4,
      pass_rate: 66.67,
      outcomes: { failed: 1, error: 0 },
    },
  ],
  cost_breakdown_cents: {
    llm: { total: 100, measured: 4, calls: 4 },
  },
  trends: [
    {
      execution_id: "execution-1",
      started_at: "2026-09-22T10:00:00Z",
      total: 4,
      pass_rate: 66.67,
      latency: { p95: 400 },
      tokens: { total_value: 1000 },
      cost_cents: { total_value: 250 },
    },
  ],
};

describe("RunAnalytics", () => {
  beforeEach(() => {
    const storage = new Map();
    vi.stubGlobal("localStorage", {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    });
    useRunAnalytics.mockReset();
    useRunAnalytics.mockReturnValue({
      data: analytics,
      isPending: false,
      isError: false,
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows the six T1 decisions and hides diagnostics by default", () => {
    render(<RunAnalytics executionId="execution-1" />);

    for (const label of [
      "Calls passed",
      "Drop-off",
      "Response time p95",
      "Cost / pass",
      "CSAT satisfied",
      "Change vs last run",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText(/1 call was not evaluated/)).toBeInTheDocument();
    expect(screen.getByText("Policy adherence")).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Weakest scenarios" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Reliability across trials" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Tool failure rate" }),
    ).not.toBeInTheDocument();
    expect(useRunAnalytics).toHaveBeenCalledWith("execution-1");
  });

  it("shows drop-off coverage when completion evidence is unavailable", () => {
    const data = structuredClone(analytics);
    const dropOff = data.dashboard.metrics.find(
      (metric) => metric.key === "drop_off",
    );
    dropOff.value = null;
    dropOff.measured = 0;
    useRunAnalytics.mockReturnValue({
      data,
      isPending: false,
      isError: false,
    });

    render(<RunAnalytics executionId="execution-1" />);

    expect(screen.getByText("0 / 4 assessed")).toBeInTheDocument();
  });

  it("shows which scenarios flip when detailed metrics are opened", () => {
    render(<RunAnalytics executionId="execution-1" />);
    fireEvent.click(
      screen.getByRole("button", { name: "Show detailed analytics" }),
    );
    const panel = screen.getByRole("region", {
      name: "Reliability across trials",
    });
    expect(within(panel).getByText("refund-flips")).toBeInTheDocument();
    expect(
      within(panel).getAllByText("Flipped between trials").length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByText(
        /2 scenarios × 2 trials · pass rate 95% range 12.5%–95.1%/,
      ),
    ).toBeInTheDocument();
  });

  it("distinguishes evaluated-trial row verdicts from the strict pass tile", () => {
    const data = structuredClone(analytics);
    data.reliability.scenarios = 5;
    data.reliability.consistent_pass = 1;
    const row = (scenario, passed, evaluated, error, verdict) => ({
      scenario_key: scenario,
      scenario,
      runs: 2,
      passed,
      evaluated,
      error,
      verdict,
    });
    data.reliability.rows = [
      row("All pass", 2, 2, 0, "passed"),
      row("Pass plus error", 1, 1, 1, "passed"),
      row("Pass plus inconclusive", 1, 1, 0, "passed"),
      row("Fail plus error", 0, 1, 1, "failed"),
      row("All error", 0, 0, 2, "not_evaluated"),
    ];
    useRunAnalytics.mockReturnValue({ data, isPending: false, isError: false });

    render(<RunAnalytics executionId="execution-1" />);
    fireEvent.click(
      screen.getByRole("button", { name: "Show detailed analytics" }),
    );
    const panel = screen.getByRole("region", {
      name: "Reliability across trials",
    });
    const tile = within(panel).getByText("Passed every trial").parentElement;
    expect(within(tile).getByText("1 / 5")).toBeInTheDocument();
    const allPass = within(panel).getByText("All pass").closest("tr");
    expect(
      within(allPass).getByText("Passed all evaluated trials"),
    ).toBeInTheDocument();
    for (const scenario of ["Pass plus error", "Pass plus inconclusive"]) {
      const row = within(panel).getByText(scenario).closest("tr");
      expect(
        within(row).getByText("Passed all evaluated trials"),
      ).toBeInTheDocument();
      expect(within(row).getByText("1 / 2")).toBeInTheDocument();
    }
    const failed = within(panel).getByText("Fail plus error").closest("tr");
    expect(
      within(failed).getByText("Failed all evaluated trials"),
    ).toBeInTheDocument();
    expect(within(failed).getByText("1 / 2")).toBeInTheDocument();
    const allError = within(panel).getByText("All error").closest("tr");
    expect(within(allError).getByText("Not evaluated")).toBeInTheDocument();
    expect(within(allError).getByText("0 / 2")).toBeInTheDocument();
  });

  it("hides provider-only charts when no provider reported them", () => {
    render(<RunAnalytics executionId="execution-1" />);
    expect(
      screen.queryByRole("region", { name: "Provider sentiment" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Provider's own success flag" }),
    ).not.toBeInTheDocument();
  });

  it("shows an honest empty state", () => {
    useRunAnalytics.mockReturnValue({
      data: { ...analytics, summary: { ...analytics.summary, total: 0 } },
      isPending: false,
      isError: false,
    });

    render(<RunAnalytics executionId="execution-1" />);
    expect(screen.getByText("No calls to analyze")).toBeInTheDocument();
  });

  it("renders the updated histograms without the retired turn-count charts", () => {
    render(<RunAnalytics executionId="execution-1" />);
    fireEvent.click(
      screen.getByRole("button", { name: "Show detailed analytics" }),
    );
    expect(
      screen.getByRole("region", { name: "CSAT distribution (0–10)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Agent response time per call" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /agrees with your evals on 50% of comparable calls \(1\/2\)/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /50% of measured calls averaged at or over the 1,?500ms target/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Avg duration by complexity"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Pass / fail by conversation length"),
    ).not.toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: /^About / }).length,
    ).toBeGreaterThan(5);
  });

  it("forwards server-provided goal-outcome filters from the chart", () => {
    const open = vi.fn();
    render(<RunAnalytics executionId="execution-1" onOpenCalls={open} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Show detailed analytics" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Show Failed calls" }));
    expect(open).toHaveBeenCalledWith({ goal_outcome: ["failed"] });
    fireEvent.click(screen.getByRole("button", { name: "Show Passed calls" }));
    expect(open).toHaveBeenLastCalledWith({ goal_outcome: ["passed"] });
    fireEvent.click(
      screen.getByRole("button", { name: "Show Escalated calls" }),
    );
    expect(open).toHaveBeenLastCalledWith({ goal_outcome: ["escalated"] });
  });

  it("opens the actual call from a performance-tail widget", () => {
    const open = vi.fn();
    render(<RunAnalytics executionId="execution-1" onOpenCall={open} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Show detailed analytics" }),
    );
    fireEvent.click(
      within(screen.getByRole("region", { name: "Slowest calls" })).getByRole(
        "button",
        { name: "Open task 1" },
      ),
    );
    expect(open).toHaveBeenCalledWith({
      id: "call-1",
      scenario: "Handle a refund",
      simulationCallType: "voice",
      provider: "livekit",
    });
  });

  it("reveals and hides detailed metrics in one click", () => {
    render(<RunAnalytics executionId="execution-1" />);
    expect(
      screen.queryByRole("region", { name: "Tool failure rate" }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Show detailed analytics" }),
    );
    expect(
      screen.getByRole("region", { name: "Tool failure rate" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Hide detailed analytics" }),
    );
    expect(
      screen.queryByRole("region", { name: "Tool failure rate" }),
    ).not.toBeInTheDocument();
  });

  const withDashboard = (overrides) => {
    useRunAnalytics.mockReturnValue({
      data: {
        ...analytics,
        dashboard: { ...analytics.dashboard, ...overrides },
      },
      isPending: false,
      isError: false,
    });
    const view = render(<RunAnalytics executionId="execution-1" />);
    fireEvent.click(
      screen.getByRole("button", { name: "Show detailed analytics" }),
    );
    return view;
  };
  const latencyTile = (label) => ({
    key: "agent_latency",
    label,
    value: 250,
    unit: "ms",
    measured: 3,
    total: 4,
  });
  const seriesRow = (extra) => ({
    label: "1",
    calls: 1,
    started_at: null,
    llm_cents: null,
    tts_cents: null,
    stt_cents: null,
    storage_cents: null,
    ...extra,
  });

  it("shows agent latency percentiles from the curve", () => {
    withDashboard({
      metrics: [
        ...analytics.dashboard.metrics,
        latencyTile("Agent response time"),
      ],
      agent_latency_percentiles: [
        { percentile: 50, value: 250 },
        { percentile: 90, value: 400 },
        { percentile: 99, value: 900 },
      ],
      latency_percentiles: [
        { percentile: 50, value: 189000 },
        { percentile: 90, value: 308100 },
        { percentile: 99, value: 338520 },
      ],
    });
    const region = screen.getByRole("region", {
      name: "Agent response time percentiles",
    });
    expect(
      within(region).getByText(
        "p50 250ms · p90 400ms · p99 900ms of per-call averages",
      ),
    ).toBeInTheDocument();
    expect(
      within(region).queryByText("No measurements recorded"),
    ).not.toBeInTheDocument();
    fireEvent.mouseMove(region.querySelector(".recharts-wrapper"), {
      clientX: 300,
      clientY: 100,
    });
    expect(
      region.querySelector(".recharts-tooltip-wrapper").textContent,
    ).toMatch(/^Percentile \d+Agent response time : \d+ms$/);
  });

  it("draws a dot for a measured call between unmeasured ones", () => {
    withDashboard({
      series: [
        seriesRow({ latency_ms: 420 }),
        seriesRow({ label: "2", latency_ms: null }),
        seriesRow({ label: "3", latency_ms: 480 }),
        seriesRow({ label: "4", latency_ms: null }),
      ],
    });
    const region = screen.getByRole("region", { name: "Agent latency" });
    expect(region.querySelectorAll(".recharts-line-dot")).toHaveLength(2);
  });

  it("ignores a legacy call-length curve", () => {
    withDashboard({
      agent_latency_percentiles: undefined,
      latency_percentiles: [{ percentile: 50, value: 189000 }],
    });
    const region = screen.getByRole("region", {
      name: "Agent response time percentiles",
    });
    expect(
      within(region).getByText("p50 - · p90 - · p99 - of per-call averages"),
    ).toBeInTheDocument();
    expect(
      within(region).getByText("No measurements recorded"),
    ).toBeInTheDocument();
  });

  it("plots task latency from latency_ms only", () => {
    const legacy = withDashboard({
      metrics: [...analytics.dashboard.metrics, latencyTile("Agent latency")],
      series: [seriesRow({ duration_ms: 60000 })],
    });
    let region = screen.getByRole("region", { name: "Agent latency" });
    expect(
      within(region).getByText("No measurements recorded"),
    ).toBeInTheDocument();
    expect(
      within(region).getByText("Agent latency per call"),
    ).toBeInTheDocument();

    legacy.unmount();
    withDashboard({
      metrics: [
        ...analytics.dashboard.metrics,
        latencyTile("Agent response time"),
      ],
      series_mode: "time_buckets",
      series: [
        seriesRow({ latency_ms: 420, duration_ms: 189000 }),
        seriesRow({ label: "2", latency_ms: 480, duration_ms: 308100 }),
      ],
    });
    region = screen.getByRole("region", { name: "Agent latency" });
    expect(
      within(region).queryByText("No measurements recorded"),
    ).not.toBeInTheDocument();
    expect(
      within(region).getByText("Agent response time · average per time bucket"),
    ).toBeInTheDocument();
    const ticks = [...region.querySelectorAll(".recharts-yAxis text")].map(
      (node) => node.textContent,
    );
    expect(ticks.length).toBeGreaterThan(0);
    expect(ticks.every((text) => text.endsWith("ms"))).toBe(true);
    const values = ticks.map((text) =>
      Number(text.replace(/ms$/, "").replace(/,/g, "")),
    );
    expect(Math.max(...values)).toBeGreaterThanOrEqual(420);
    fireEvent.mouseMove(region.querySelector(".recharts-wrapper"), {
      clientX: 300,
      clientY: 100,
    });
    expect(
      region.querySelector(".recharts-tooltip-wrapper").textContent,
    ).toContain("Agent response time : 420ms");
  });

  it("uses the tile label in the per-call task latency subtitle", () => {
    withDashboard({
      metrics: [
        ...analytics.dashboard.metrics,
        latencyTile("Agent response time"),
      ],
      series: [seriesRow({ latency_ms: 420 })],
    });
    const region = screen.getByRole("region", { name: "Agent latency" });
    expect(
      within(region).getByText("Agent response time per call"),
    ).toBeInTheDocument();
  });

  it("labels the latency distribution row from the tile label", () => {
    const row = {
      key: "latency_ms",
      measured: 3,
      average: 300,
      max: 900,
      p50: 250,
      p90: 400,
      p99: 880,
    };
    const voice = withDashboard({
      metrics: [...analytics.dashboard.metrics, latencyTile("Agent latency")],
      distributions: [row],
    });
    let region = screen.getByRole("region", { name: "Distribution summary" });
    expect(within(region).getByText("Agent latency")).toBeInTheDocument();
    expect(screen.queryByText("End-to-end latency")).not.toBeInTheDocument();
    expect(within(region).getByText("400ms")).toBeInTheDocument();

    voice.unmount();
    withDashboard({
      metrics: [
        ...analytics.dashboard.metrics,
        latencyTile("Agent response time"),
      ],
      distributions: [row],
    });
    region = screen.getByRole("region", { name: "Distribution summary" });
    expect(within(region).getByText("Agent response time")).toBeInTheDocument();
    expect(within(region).queryByText("Agent latency")).not.toBeInTheDocument();
  });

  it("hides the legacy call-length row", () => {
    withDashboard({
      distributions: [
        {
          key: "latency_ms",
          measured: 3,
          average: 300,
          max: 900,
          p50: 250,
          p90: 400,
          p99: 880,
        },
        {
          key: "end_to_end_ms",
          measured: 4,
          average: 189000,
          max: 338520,
          p50: 189000,
          p90: 308100,
          p99: 338520,
        },
      ],
    });
    const region = screen.getByRole("region", { name: "Distribution summary" });
    expect(within(region).getByText("Agent latency")).toBeInTheDocument();
    expect(
      within(region).queryByText("End-to-end latency"),
    ).not.toBeInTheDocument();
    expect(within(region).queryByText("end_to_end_ms")).not.toBeInTheDocument();
    expect(within(region).queryByText(/308,100/)).not.toBeInTheDocument();
  });

  it("describes latency charts as agent latency", () => {
    expect(CHART_GUIDE.task_latency).toBe(
      "Each call's average agent response time in the order it ran. Random spikes point at flaky infrastructure; a steady climb means the agent is doing more of something over time (retries, context growth); a step change usually means a new tool or model kicked in mid-run.",
    );
    expect(CHART_GUIDE.percentiles).toBe(
      "Every call's average agent response time, sorted: read across to a percentile, up to the wait. p50 is typical, p90 is the slower 10% of calls. These are per-call averages, so a single long pause inside an otherwise quick call does not show here.",
    );
    expect(CHART_GUIDE.response_time).toBe(
      "Each call's average time for the agent to start replying after the caller stops talking. Red buckets are at or over the target (1.5 s for voice, 3 s for chat), where callers start to notice silence. A second hump on the right usually means one tool or prompt path is consistently slow.",
    );
    expect(CHART_GUIDE.slowest).toBe(
      "The eight longest calls. These drive the duration p90 and p99. If the top ones share a scenario, you've found a pattern, not a one-off.",
    );
  });
});
