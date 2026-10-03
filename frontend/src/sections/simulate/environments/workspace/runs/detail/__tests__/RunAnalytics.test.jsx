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
        key: "total",
        label: "Total calls",
        value: 4,
        unit: "number",
        measured: 4,
        total: 4,
      },
      {
        key: "csat",
        label: "Avg CSAT score",
        value: null,
        unit: "number",
        measured: 0,
        total: 4,
      },
    ],
    breakdowns: [
      {
        key: "call_success",
        total: 4,
        headline: { label: "successful", count: 2, share: 50 },
        segments: [
          { label: "successful", count: 2, share: 50, statuses: ["passed"] },
          {
            label: "unsuccessful",
            count: 1,
            share: 25,
            statuses: ["failed", "error"],
          },
          { label: "unknown", count: 1, share: 25, statuses: ["inconclusive"] },
        ],
      },
      {
        key: "goal_outcome",
        total: 4,
        segments: [
          { label: "passed", count: 2, share: 50 },
          { label: "failed", count: 1, share: 25 },
          { label: "inconclusive", count: 1, share: 25 },
        ],
      },
    ],
    evaluation_summary: { graders: 1, passed: 2, measured: 3 },
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
      bins: [{ label: "4", lower: 3.5, upper: 4.5, count: 2, danger: true }],
      agreement: { compared: 2, agreed: 1, percent: 50 },
    },
    agent_response_time: {
      measured: 2,
      total: 4,
      target_ms: 550,
      p50: 550,
      p95: 590,
      at_or_above_target: 1,
      at_or_above_target_percent: 50,
      bins: [
        { label: "550ms", lower: 550, upper: 575, count: 1, danger: true },
      ],
    },
    pipeline_cost: [],
    tools: { total_invocations: 0, total_tools: 0, volume: [], failures: [] },
    use_case_risk: [
      {
        goal: "Handle a refund",
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
      goal: "Handle a refund",
      total: 4,
      pass_rate: 66.67,
      outcomes: { failed: 1, error: 0 },
    },
  ],
  turn_distribution: [
    { turn_count: 4, passed: 2, failed: 1, error: 0, inconclusive: 1 },
  ],
  evaluations: [
    {
      id: "eval-1",
      name: "Policy adherence",
      passed: 2,
      measured: 3,
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

  it("renders the v3 summary and native breakdowns", () => {
    render(<RunAnalytics executionId="execution-1" />);

    expect(screen.getAllByText("66.7%").length).toBeGreaterThan(0);
    expect(screen.getByText("Policy adherence")).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Use case risk" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Tool failure rate" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Not recorded")).toBeInTheDocument();
    expect(screen.queryByText("Failure attribution")).not.toBeInTheDocument();
    expect(screen.queryByText(/critical failures/i)).not.toBeInTheDocument();
    expect(useRunAnalytics).toHaveBeenCalledWith("execution-1");
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
        /50% of measured calls were at or over the 550ms target/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Avg duration by complexity"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Pass / fail by conversation length"),
    ).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^About / })).toHaveLength(17);
  });

  it("forwards server-provided status filters from the success chart", () => {
    const open = vi.fn();
    render(<RunAnalytics executionId="execution-1" onOpenCalls={open} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Show Unsuccessful calls" }),
    );
    expect(open).toHaveBeenCalledWith({ status: ["failed", "error"] });
    fireEvent.click(
      screen.getByRole("button", { name: "Show Successful calls" }),
    );
    expect(open).toHaveBeenLastCalledWith({ status: ["passed"] });
  });

  it("opens the actual call from a performance-tail widget", () => {
    const open = vi.fn();
    render(<RunAnalytics executionId="execution-1" onOpenCall={open} />);
    fireEvent.click(
      within(screen.getByRole("region", { name: "Slowest tasks" })).getByRole(
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

  it("shows all widgets even when an older saved layout hides them", () => {
    localStorage.setItem(
      "simulation-analytics-layout-v1:execution-1",
      JSON.stringify({
        hidden: ["tools_failure"],
        views: [],
        active: "Custom",
      }),
    );
    const { rerender } = render(<RunAnalytics executionId="execution-1" />);
    expect(
      screen.queryByRole("region", { name: "Tool failure rate" }),
    ).toBeInTheDocument();
    rerender(<RunAnalytics executionId="execution-2" />);
    expect(
      screen.getByRole("region", { name: "Tool failure rate" }),
    ).toBeInTheDocument();
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
    return render(<RunAnalytics executionId="execution-1" />);
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
    const region = screen.getByRole("region", { name: "Latency percentiles" });
    expect(
      within(region).getByText("p50 250ms · p90 400ms · p99 900ms"),
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
    const region = screen.getByRole("region", { name: "Task latency" });
    expect(region.querySelectorAll(".recharts-line-dot")).toHaveLength(2);
  });

  it("ignores a legacy call-length curve", () => {
    withDashboard({
      agent_latency_percentiles: undefined,
      latency_percentiles: [{ percentile: 50, value: 189000 }],
    });
    const region = screen.getByRole("region", { name: "Latency percentiles" });
    expect(
      within(region).getByText("p50 - · p90 - · p99 -"),
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
    let region = screen.getByRole("region", { name: "Task latency" });
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
    region = screen.getByRole("region", { name: "Task latency" });
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
    const region = screen.getByRole("region", { name: "Task latency" });
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
      "Each task's agent latency (the agent's average response time per turn in that task), in the order the tasks ran. Random spikes = flaky infra; a steady climb = something the agent is doing more of over time (retries, context growth); a step change = usually a new tool or model kicking in mid-run.",
    );
    expect(CHART_GUIDE.percentiles).toBe(
      "Every measured task's agent latency (the agent's average response time per turn in that task), sorted: read across to a percentile, up to the latency. p50 = typical; p90 = the slower 10% of tasks (the ones your SLO is really written for); p99 = your worst tail. A curve that bends sharply upward near the right edge means a small set of tasks is dragging the tail.",
    );
    expect(CHART_GUIDE.response_time).toBe(
      "Each call's average agent response time per turn (for voice, the gap between the caller finishing and the agent starting to speak), the same per-call figure as the agent latency tile. Red buckets are at or over the 550ms target, where callers start to notice silence. A second hump on the right usually means one tool or prompt path is consistently slow.",
    );
    expect(CHART_GUIDE.slowest).toBe(
      "The eight tasks that ran longest, by wall-clock duration. If the top ones share a persona or use case, you've found a pattern, not a one-off.",
    );
  });
});
