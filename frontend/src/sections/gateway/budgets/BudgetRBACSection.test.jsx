import React from "react";
import PropTypes from "prop-types";
import { HelmetProvider } from "react-helmet-async";
import { describe, it, expect, vi, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { render, screen } from "src/utils/test-utils";
import axiosInstance from "src/utils/axios";
import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";

import BudgetRBACSection from "./BudgetRBACSection";
import { lastGrid } from "../providers/__tests__/grid-mock";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../testing/adminOnly";

// The gateway admin role check (useGatewayAdmin): setting and removing a budget is
// Owner or Admin only. The cases below run as an Admin unless one says
// otherwise.
let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

vi.mock(
  "ag-grid-react",
  async () => await import("../providers/__tests__/grid-mock"),
);

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
  useUpdateConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

// The bar reads the period's spend from the request logs (R7-BUDGET NG-32),
// never the stored "spent" above.
vi.mock("./hooks/useBudgetSpend", () => ({
  useBudgetSpend: () => ({
    data: { monthly: { total_spend: 125, per_user: {}, per_key: {} } },
    isLoading: false,
    isError: false,
  }),
}));

vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({
    data: {
      total_cost: { value: 50 },
      total_requests: { value: 10 },
    },
  }),
}));

// The page frame sets the document title.
const renderPage = () =>
  render(
    <HelmetProvider>
      <BudgetRBACSection />
    </HelmetProvider>,
  );

describe("BudgetRBACSection", () => {
  it("renders a stored Hard limit level from canonical budget config fields", () => {
    renderPage();

    expect(
      screen.getByRole("heading", { level: 1, name: "Budgets" }),
    ).toBeInTheDocument();
    const [row] = lastGrid.props.rowData;
    expect(row).toMatchObject({
      label: "Hard Limit",
      limit: 1000,
      action: "block",
      alertThreshold: 75,
    });
    // The stored `spent` is not a spend record: the period's spend
    // comes from the request logs.
    expect(row.spend).toBe(125);
  });

  // Budget caps are upper bounds and cannot round down.
  it.each([
    [0.087686, "$0.087686"],
    [12.341, "$12.341"],
    // R3-01/R3-03: discard binary noise before ceiling a cap.
    [12.340000000000002, "$12.340000000000002"],
    [1.15, "$1.15"],
  ])("does not understate the budget cap %s", (limit, expected) => {
    renderPage();
    const UsedCell = lastGrid.props.columnDefs.find(
      (col) => col.colId === "used",
    ).cellRenderer;
    const { container } = render(
      <UsedCell data={{ group: "org", limit, spend: 0 }} />,
    );
    expect(container).toHaveTextContent(`$0.00 / ${expected}`);
  });

  it.each(NOT_ADMIN_LEVELS)(
    "%s reads the budgets with Set Budget and Remove disabled",
    (_who, level) => {
      mockOrgLevel = level;
      renderPage();
      // R3-02: the cap is grouped; the plain spend display is unchanged.
      expect(screen.getByText("$125.00 / $1,000.00")).toBeInTheDocument();
      expectAdminOnly(screen.getByRole("button", { name: "Set budget" }));
      expectAdminOnly(screen.getByRole("button", { name: /remove budget/i }));
      mockOrgLevel = 8;
    },
  );

  it.each(ADMIN_LEVELS)("%s can set and remove budgets", (_who, level) => {
    mockOrgLevel = level;
    renderPage();
    expectEditable(screen.getByRole("button", { name: "Set budget" }));
    expectEditable(screen.getByRole("button", { name: /remove budget/i }));
    mockOrgLevel = 8;
  });
});

// The page reads its spend from GET /agentcc/spend-summary/ (useBudgetSpend,
// mocked above for the page). Outside production builds the axios contract
// guard refuses a query param the contract does not declare before the
// request is sent; that left the old Recent Activity tab empty, as it sent
// an undeclared gateway_id. The spend read sends only declared params.
describe("BudgetRBACSection spend read", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reads each budget period's spend with a query the API contract accepts", async () => {
    const get = vi
      .spyOn(axiosInstance, "get")
      .mockResolvedValue({ data: { result: { orgs: {} } } });
    const { useBudgetSpend } = await vi.importActual("./hooks/useBudgetSpend");
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    function wrapper({ children }) {
      return (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      );
    }
    wrapper.propTypes = { children: PropTypes.node };

    const { result } = renderHook(() => useBudgetSpend(["monthly"]), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    const [url, config] = get.mock.calls[0];
    expect(config.params).not.toHaveProperty("gateway_id");
    const checked = validateContractedRequestConfig({
      url,
      method: "get",
      ...config,
    });
    expect(checked).toMatchObject({ ok: true });
    expect(checked.skipped).toBeUndefined();
  });
});
