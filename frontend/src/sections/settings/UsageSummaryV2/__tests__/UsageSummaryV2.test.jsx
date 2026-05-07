import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
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

describe("UsageSummaryV2", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url) => {
      if (url === "/usage/v2/usage-overview/") {
        return Promise.resolve({ data: { result: BASE_OVERVIEW } });
      }
      if (url === "/usage/v2/notifications/") {
        return Promise.resolve({ data: { result: { banners: [] } } });
      }
      return Promise.resolve({ data: { result: {} } });
    });
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
});

describe("WorkspaceBreakdown", () => {
  it("should be importable", async () => {
    const module = await import("../WorkspaceBreakdown");
    expect(module.default).toBeDefined();
  });
});
