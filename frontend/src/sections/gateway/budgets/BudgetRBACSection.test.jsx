import { describe, it, expect, vi, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "src/utils/test-utils";
import axiosInstance from "src/utils/axios";
import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";

import BudgetRBACSection from "./BudgetRBACSection";

import RequestDetailDrawer from "../logs/RequestDetailDrawer";
import useRequestDetail from "../logs/hooks/useRequestDetail";

vi.mock("../logs/hooks/useRequestDetail");
vi.mock("../guardrails/FeedbackWidget", () => ({ default: () => null }));

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

  it.each([
    ["priced", "0.5", { cost_source: "catalog" }, "$0.5000"],
    ["free", 0, { cost_source: "org_custom" }, "$0.00"],
    ["unpriced", 0, { cost_source: "unpriced" }, "Unpriced"],
    ["empty unpriced", null, { cost_source: "unpriced" }, "Unpriced"],
    ["unknown source", 0, {}, "$0.00"],
  ])(
    "matches the drawer for a %s request",
    async (_, cost, metadata, expected) => {
      const log = {
        id: "cost-log",
        model: "test-model",
        status_code: 200,
        cost,
        metadata,
      };
      mockBudgetTab = "audit";
      vi.spyOn(axiosInstance, "get").mockResolvedValue({
        data: { results: [log] },
      });
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const view = render(
        <QueryClientProvider client={client}>
          <BudgetRBACSection />
        </QueryClientProvider>,
      );
      const row = await screen.findByRole("row", { name: /test-model/ });
      const rowCost = within(row).getByText(expected).textContent;
      view.unmount();
      vi.mocked(useRequestDetail).mockReturnValue({ data: { result: log } });
      render(<RequestDetailDrawer open logId={log.id} onClose={vi.fn()} />);
      expect(
        within(screen.getByText("Cost").parentElement).getByText(rowCost),
      ).toBeInTheDocument();
    },
  );

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
    expect(screen.getByText("Budget levels")).toBeInTheDocument();
    expect(screen.getByText("Hard Limit")).toBeInTheDocument();
    expect(screen.getByText("$125.00 / $1.0K")).toBeInTheDocument();
  });
});
