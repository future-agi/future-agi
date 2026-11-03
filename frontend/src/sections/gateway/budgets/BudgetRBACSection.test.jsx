import { describe, it, expect, vi, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import axiosInstance from "src/utils/axios";
import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";

import BudgetRBACSection from "./BudgetRBACSection";

let mockBudgetTab;

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useParams: () => ({ tab: mockBudgetTab }),
    useNavigate: () => vi.fn(),
  };
});

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    isLoading: false,
  }),
}));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    isLoading: false,
    data: {
      budgets: {
        hard_limit: {
          limit: 1000,
          spent: 125,
          alertThreshold: 75,
          onExceed: "block",
        },
      },
      auth: { enabled: true },
      rbac: {},
    },
  }),
  useRemoveBudget: () => ({ mutate: vi.fn(), isPending: false }),
  useSetBudget: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({
    data: {
      total_cost: { value: 50 },
      total_requests: { value: 10 },
    },
  }),
}));

describe("BudgetRBACSection", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockBudgetTab = undefined;
  });

  it("loads Recent Activity with a query the API contract accepts", async () => {
    mockBudgetTab = "audit";
    const get = vi.spyOn(axiosInstance, "get").mockResolvedValue({
      data: { results: [] },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <BudgetRBACSection />
      </QueryClientProvider>,
    );

    await vi.waitFor(() => expect(get).toHaveBeenCalled());
    const [url] = get.mock.calls[0];
    // Outside production builds the axios contract guard rejects undeclared
    // query params before the request is sent, so the tab stayed empty.
    expect(validateContractedRequestConfig({ url, method: "get" }).ok).toBe(
      true,
    );
  });

  it("renders budget dashboard from canonical budget config fields", () => {
    render(<BudgetRBACSection />);

    expect(screen.getByText("Budgets")).toBeInTheDocument();
    expect(screen.getByText("Budget Levels")).toBeInTheDocument();
    expect(screen.getByText("Hard Limit")).toBeInTheDocument();
    expect(screen.getByText("$125.00 / $1.0K")).toBeInTheDocument();
  });
});
