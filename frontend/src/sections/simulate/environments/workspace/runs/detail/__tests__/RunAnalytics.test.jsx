import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

const useRunAnalytics = vi.fn();
vi.mock("src/api/simulate-environments/runAnalytics", () => ({
  useRunAnalytics: (...args) => useRunAnalytics(...args),
}));

const { default: RunAnalytics } = await import("../RunAnalytics");

const analytics = {
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
    useRunAnalytics.mockReset();
    useRunAnalytics.mockReturnValue({
      data: analytics,
      isPending: false,
      isError: false,
    });
  });

  it("renders the v3 summary and native breakdowns", () => {
    render(<RunAnalytics executionId="execution-1" />);

    expect(screen.getAllByText("66.7%").length).toBeGreaterThan(0);
    expect(screen.getByText("Handle a refund")).toBeInTheDocument();
    expect(screen.getByText("Policy adherence")).toBeInTheDocument();
    expect(
      screen.getByText("Evaluation: Policy adherence"),
    ).toBeInTheDocument();
    expect(screen.getByText("Provider performance")).toBeInTheDocument();
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
    expect(
      screen.getByText("No completed calls to analyze"),
    ).toBeInTheDocument();
  });
});
