import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cloneElement } from "react";
import { fireEvent, render, screen, within } from "src/utils/test-utils";

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
    latency_percentiles: [],
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

  it("hides and restores widgets and saves the selected layout", async () => {
    render(<RunAnalytics executionId="execution-1" />);
    fireEvent.click(
      screen.getByRole("button", { name: "Hide Tool failure rate" }),
    );
    expect(
      screen.queryByRole("region", { name: "Tool failure rate" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Save view/ }));
    fireEvent.change(screen.getByLabelText("View name"), {
      target: { value: "Support review" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save", exact: true }));
    const saved = JSON.parse(
      localStorage.getItem("simulation-analytics-layout-v1:execution-1"),
    );
    expect(saved.views).toEqual([
      { name: "Support review", hidden: ["tools_failure"] },
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "Hidden (1)" }));
    fireEvent.click(
      screen.getByRole("menuitem", { name: "Tool failure rate" }),
    );
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    expect(
      await screen.findByRole("region", { name: "Tool failure rate" }),
    ).toBeInTheDocument();
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

  it("does not reuse another run's saved layout", () => {
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
    ).not.toBeInTheDocument();
    rerender(<RunAnalytics executionId="execution-2" />);
    expect(
      screen.getByRole("region", { name: "Tool failure rate" }),
    ).toBeInTheDocument();
  });
});
