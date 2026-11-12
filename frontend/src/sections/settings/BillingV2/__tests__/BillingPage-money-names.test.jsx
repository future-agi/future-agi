import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "src/utils/test-utils";

import BillingPageV2 from "../BillingPage";

// Settings > Billing: every amount has two decimals (never $128.4 or $50),
// Invoice History names the plan instead of its code ("payg"), the last
// column is titled only when an invoice has something to show in it, and a
// total-spend budget is written in the same dollars as the period total.

const mockGet = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mockGet(...args),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
  endpoints: {
    settings: {
      v2: {
        billingOverview: "/usage/v2/billing-overview/",
        invoices: "/usage/v2/invoices/",
        invoiceDetail: (id) => `/usage/v2/invoices/${id}/`,
        notifications: "/usage/v2/notifications/",
        budgets: "/usage/v2/budgets/",
      },
    },
  },
}));

vi.mock("../useCentralBilling", () => ({
  useCentralBilling: () => ({
    isCloud: true,
    creditsAvailable: false,
    isLoading: false,
  }),
}));

// Budgets and payment methods are covered in billing-dialogs-units.test.jsx;
// the budgets section is shown only where a budget is compared with the
// Current Period total.
const shown = vi.hoisted(() => ({ budgets: false }));
vi.mock("../BudgetManager", async (importOriginal) => {
  const { default: Budgets } = await importOriginal();
  return { default: () => (shown.budgets ? <Budgets /> : null) };
});
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));
vi.mock("../PaymentMethods", () => ({ default: () => null }));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// An owner account: Platform fee + AI credits + tracing = 128.4.
const BILLING = {
  data: {
    result: {
      plan: "payg",
      period: "Nov 1 - Nov 30, 2026",
      total: 128.4,
      line_items: [
        { description: "Platform fee", amount: 50 },
        { description: "AI credits usage (12,480 requests)", amount: 61.25 },
        { description: "Tracing events", amount: 17.15 },
      ],
    },
  },
};

const OCT = "2026-10-01 — 2026-10-31";
const SEP = "2026-09-01 — 2026-09-30";

function invoices({ pdf = false } = {}) {
  return {
    data: {
      result: {
        invoices: [
          {
            id: "inv-oct",
            period_start: "2026-10-01",
            period_end: "2026-10-31",
            plan: "payg",
            total: 212.9,
            status: "paid",
            ...(pdf && { stripe_pdf_url: "https://files.example/inv-oct.pdf" }),
          },
          {
            id: "inv-sep",
            period_start: "2026-09-01",
            period_end: "2026-09-30",
            plan: "boost",
            total: 98.1,
            status: "paid",
          },
        ],
      },
    },
  };
}

const DETAIL = {
  data: {
    result: {
      line_items: [
        {
          line_type: "usage",
          dimension: "storage",
          description: "Storage",
          quantity: 3,
          unit: "GB",
          unit_price: 1.5,
          amount: 4.5,
        },
      ],
    },
  },
};

// A total-spend budget is stored in US dollars with four decimals.
const SPEND_BUDGET = {
  id: "budget-spend",
  name: "Total spend hard stop",
  scope: "total_spend",
  threshold_value: "2500.0000",
  action: "pause",
};

// Platform fee + AI credits = 2500, the budget's threshold.
const BILLING_AT_BUDGET = {
  data: {
    result: {
      plan: "payg",
      period: "Nov 1 - Nov 30, 2026",
      total: 2500,
      line_items: [
        { description: "Platform fee", amount: 50 },
        { description: "AI credits usage (490,000 requests)", amount: 2450 },
      ],
    },
  },
};

function mockApi({ pdf = false, billing = BILLING, budgets = [] } = {}) {
  mockGet.mockImplementation((url) => {
    if (url.includes("billing-overview")) return Promise.resolve(billing);
    if (url.includes("budgets"))
      return Promise.resolve({ data: { result: { budgets } } });
    if (url.includes("/invoices/inv-")) return Promise.resolve(DETAIL);
    if (url.includes("invoices")) return Promise.resolve(invoices({ pdf }));
    if (url.includes("notifications"))
      return Promise.resolve({ data: { result: { banners: [] } } });
    return Promise.resolve({ data: { result: {} } });
  });
}

function renderBilling() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <BillingPageV2 />
    </QueryClientProvider>,
  );
}

describe("Billing amounts show cents", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApi();
  });

  it("writes the Current Period total and every line with two decimals", async () => {
    renderBilling();
    await screen.findByText("Current period");

    // Estimated total and the Total row.
    expect(screen.getAllByText("$128.40")).toHaveLength(2);
    expect(screen.getByText("$50.00")).toBeInTheDocument();
    expect(screen.getByText("$61.25")).toBeInTheDocument();
    expect(screen.getByText("$17.15")).toBeInTheDocument();
    expect(screen.queryByText("$128.4")).toBeNull();
    expect(screen.queryByText("$50")).toBeNull();
  });

  it("writes invoice totals, rates and line amounts with two decimals", async () => {
    renderBilling();
    const oct = (await screen.findByText(OCT)).closest("tr");
    const sep = screen.getByText(SEP).closest("tr");
    expect(within(oct).getByText("$212.90")).toBeInTheDocument();
    expect(within(sep).getByText("$98.10")).toBeInTheDocument();

    fireEvent.click(oct);
    expect(await screen.findByText("$1.50")).toBeInTheDocument();
    expect(screen.getByText("$4.50")).toBeInTheDocument();
    expect(screen.queryByText("$1.5")).toBeNull();
    expect(screen.queryByText("$4.5")).toBeNull();
  });
});

describe("Invoice History names", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("names each invoice's plan the way Usage Summary does, not by its code", async () => {
    mockApi();
    renderBilling();
    const oct = (await screen.findByText(OCT)).closest("tr");
    const sep = screen.getByText(SEP).closest("tr");

    expect(within(oct).getByText("Pay-as-you-go")).toBeInTheDocument();
    expect(within(sep).getByText("Pay-as-you-go + Boost")).toBeInTheDocument();
    expect(screen.queryByText("payg")).toBeNull();
    expect(screen.queryByText("boost")).toBeNull();
  });

  it("leaves the last column untitled when no invoice has a PDF to download", async () => {
    mockApi();
    renderBilling();
    await screen.findByText(OCT);

    expect(screen.queryByTitle("Download PDF")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Actions" })).toBeNull();
    // The column stays, so each row and its detail row keep their cells.
    expect(
      screen.getAllByRole("columnheader").map((th) => th.textContent),
    ).toEqual(["", "Period", "Plan", "Amount", "Status", ""]);
  });

  it("titles the column Actions when an invoice has a PDF", async () => {
    mockApi({ pdf: true });
    renderBilling();
    await screen.findByText(OCT);

    expect(screen.getByTitle("Download PDF")).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "Actions" }),
    ).toBeInTheDocument();
  });
});

// A total-spend threshold is in US dollars: budget enforcement compares it
// with the period's invoice total, the total Current Period shows.
describe("A total-spend budget reads like the Current Period total", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    shown.budgets = true;
  });

  afterEach(() => {
    shown.budgets = false;
  });

  it("writes a stored 2500.0000 threshold as $2,500.00, like the $2,500.00 Current Period total", async () => {
    mockApi({ billing: BILLING_AT_BUDGET, budgets: [SPEND_BUDGET] });
    renderBilling();

    const row = await screen.findByTestId("usage-budget-row");
    const estimate = screen.getByText("Estimated total").parentElement;
    expect(within(estimate).getByText("$2,500.00")).toBeInTheDocument();
    expect(
      within(row).getByText("Total Spend: $2,500.00 → Pause Usage"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/\$2500\.00/)).toBeNull();
  });
});
