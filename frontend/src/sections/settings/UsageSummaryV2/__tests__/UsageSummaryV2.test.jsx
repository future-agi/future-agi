import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockGet = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => mockGet(...args) },
  endpoints: {
    settings: {
      v2: {
        usageOverview: "/usage/v2/usage-overview/",
        usageTimeSeries: "/usage/v2/usage-time-series/",
        usageWorkspaceBreakdown: "/usage/v2/usage-workspace-breakdown/",
        notifications: "/usage/v2/notifications/",
      },
    },
  },
}));

vi.mock("src/utils/format-number", () => ({
  fCurrency: (val) => `$${Number(val || 0).toFixed(2)}`,
  fUsage: (val, unit) => `${Number(val || 0).toLocaleString()} ${unit}`,
}));

vi.mock("react-apexcharts", () => ({
  default: () => null,
}));

vi.mock("../UsageChart", () => ({
  default: () => null,
}));

vi.mock("../WorkspaceBreakdown", () => ({
  default: () => null,
}));

function renderWithQuery(ui) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const BASE_OVERVIEW = {
  plan: "free",
  billing_period_start: "2026-05-01",
  billing_period_end: "2099-05-31",
  total_with_platform: 0,
  total_estimated_cost: 0,
  dimensions: [],
};

function mockUsageOverview(overview) {
  mockGet.mockImplementation((url) => {
    if (url === "/usage/v2/usage-overview/") {
      return Promise.resolve({ data: { result: overview } });
    }
    if (url === "/usage/v2/notifications/") {
      return Promise.resolve({ data: { result: { banners: [] } } });
    }
    return Promise.resolve({ data: { result: {} } });
  });
}

describe("UsageSummaryV2", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUsageOverview(BASE_OVERVIEW);
  });

  it("should be importable", async () => {
    const module = await import("../UsageSummaryV2");
    expect(module.default).toBeDefined();
  });

  it("does not show countdown chip for lifetime free plan", async () => {
    const { default: UsageSummaryV2 } = await import("../UsageSummaryV2");
    renderWithQuery(<UsageSummaryV2 />);

    expect(await screen.findByText("Usage & Billing")).toBeInTheDocument();
    expect(screen.queryByText(/days left/i)).not.toBeInTheDocument();
  });

  it("shows exact discrete usage and billable amount on dimension cards", async () => {
    const { default: UsageSummaryV2 } = await import("../UsageSummaryV2");
    mockUsageOverview({
      ...BASE_OVERVIEW,
      dimensions: [
        {
          key: "tracing_events",
          display_name: "Tracing Events",
          display_unit: "events",
          current_usage: 30531,
          current_usage_raw: 30531,
          free_allowance: 10000,
          projected_usage: 30531,
          estimated_cost: 13.14,
          tier_breakdown: [
            { range: "0 to 10,000 events", rate: "$0.00", cost: 0 },
            { range: "Above 10,000 events", rate: "$0.00064", cost: 13.14 },
          ],
        },
      ],
    });

    renderWithQuery(<UsageSummaryV2 />);

    expect(await screen.findByText("30,531 events")).toBeInTheDocument();
    expect(
      screen.getByText(
        (content) =>
          content.includes("10,000 free") && content.includes("20,531 billed"),
      ),
    ).toBeInTheDocument();
  });

  it("rounds projected usage for all discrete units", async () => {
    const { default: UsageSummaryV2 } = await import("../UsageSummaryV2");
    mockUsageOverview({
      ...BASE_OVERVIEW,
      dimensions: [
        {
          key: "tracing_events",
          display_name: "Tracing Events",
          display_unit: "events",
          current_usage: 700,
          current_usage_raw: 700,
          free_allowance: 10000,
          projected_usage: 704.4,
          estimated_cost: 0,
          tier_breakdown: [
            { range: "0 to 10,000 events", rate: "$0.00", cost: 0 },
          ],
        },
        {
          key: "gateway_requests",
          display_name: "Gateway Requests",
          display_unit: "requests",
          current_usage: 100,
          current_usage_raw: 100,
          free_allowance: 0,
          projected_usage: 101.6,
          estimated_cost: 0,
          tier_breakdown: [],
        },
        {
          key: "gateway_cache_hits",
          display_name: "Gateway Cache Hits",
          display_unit: "hits",
          current_usage: 200,
          current_usage_raw: 200,
          free_allowance: 0,
          projected_usage: 200.5,
          estimated_cost: 0,
          tier_breakdown: [],
        },
        {
          key: "text_sim_tokens",
          display_name: "Text Simulation",
          display_unit: "tokens",
          current_usage: 990,
          current_usage_raw: 990,
          free_allowance: 0,
          projected_usage: 999.4,
          estimated_cost: 0,
          tier_breakdown: [],
        },
      ],
    });

    renderWithQuery(<UsageSummaryV2 />);

    expect(
      await screen.findByText(/~704 events projected/),
    ).toBeInTheDocument();
    expect(screen.getByText(/~102 requests projected/)).toBeInTheDocument();
    expect(screen.getByText(/~201 hits projected/)).toBeInTheDocument();
    expect(screen.getByText(/~999 tokens projected/)).toBeInTheDocument();
    expect(
      screen.queryByText(/704\.4 events projected/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/101\.6 requests projected/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/200\.5 hits projected/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/999\.4 tokens projected/),
    ).not.toBeInTheDocument();
  });

  it("does not display raw storage bytes as GB", async () => {
    const { default: UsageSummaryV2 } = await import("../UsageSummaryV2");
    mockUsageOverview({
      ...BASE_OVERVIEW,
      dimensions: [
        {
          key: "storage",
          display_name: "Storage",
          display_unit: "GB",
          current_usage: 0.0853,
          current_usage_raw: 91583729,
          free_allowance: 50,
          projected_usage: 0.1,
          estimated_cost: 0,
          tier_breakdown: [{ range: "0 to 50 GB", rate: "$0.00", cost: 0 }],
        },
      ],
    });

    renderWithQuery(<UsageSummaryV2 />);

    expect(await screen.findByText("0.1 GB")).toBeInTheDocument();
    expect(screen.queryByText(/91\.6M GB/)).not.toBeInTheDocument();
  });

  it("scales free tier savings with the selected period", async () => {
    const { default: UsageSummaryV2 } = await import("../UsageSummaryV2");
    mockUsageOverview({
      ...BASE_OVERVIEW,
      dimensions: [
        {
          key: "tracing_events",
          display_name: "Tracing Events",
          display_unit: "events",
          current_usage: 150000,
          current_usage_raw: 150000,
          free_allowance: 10000,
          projected_usage: 150000,
          estimated_cost: 0,
          tier_breakdown: [
            { range: "0 to 10,000 events", rate: "$0.00", cost: 0 },
            { range: "Above 10,000 events", rate: "$0.00064", cost: 12.8 },
          ],
        },
      ],
    });

    renderWithQuery(<UsageSummaryV2 />);

    expect(await screen.findByText("$6.40")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "3M" }));

    expect(await screen.findByText("$19.20")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "6M" }));

    expect(await screen.findByText("$38.40")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "12M" }));

    expect(await screen.findByText("$76.80")).toBeInTheDocument();
  });
});

describe("WorkspaceBreakdown", () => {
  it("should be importable", async () => {
    const module = await import("../WorkspaceBreakdown");
    expect(module.default).toBeDefined();
  });
});
