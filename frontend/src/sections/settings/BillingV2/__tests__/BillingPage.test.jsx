import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Billing writes require authority in the selected organization.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mockGet(...args),
    post: (...args) => mockPost(...args),
    put: (...args) => mockPut(...args),
    delete: (...args) => mockDelete(...args),
  },
  endpoints: {
    settings: {
      v2: {
        billingOverview: "/usage/v2/billing-overview/",
        invoices: "/usage/v2/invoices/",
        invoiceDetail: (id) => `/usage/v2/invoices/${id}/`,
        notifications: "/usage/v2/notifications/",
        budgets: "/usage/v2/budgets/",
        budgetDetail: (id) => `/usage/v2/budgets/${id}/`,
        paymentMethods: "/usage/v2/payment-methods/",
        paymentMethodSetupIntent: "/usage/v2/payment-methods/setup-intent/",
        paymentMethodDefault: (id) =>
          `/usage/v2/payment-methods/${id}/default/`,
        paymentMethodDelete: (id) => `/usage/v2/payment-methods/${id}/`,
      },
    },
  },
}));

// These tests cover the cloud platform plan; BillingPage-gateway.test covers
// the AI Gateway credits on this page.
vi.mock("../useCentralBilling", () => ({
  useCentralBilling: () => ({
    isCloud: true,
    creditsAvailable: false,
    isLoading: false,
  }),
}));

vi.mock("src/utils/format-number", () => ({
  fCurrency: (val, compact) => `$${Number(val || 0).toFixed(compact ? 4 : 2)}`,
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/pages/dashboard/settings/stripeVariables", () => ({
  stripePromise: Promise.resolve({
    confirmCardSetup: vi
      .fn()
      .mockResolvedValue({ setupIntent: { status: "succeeded" } }),
  }),
}));

// billing-overview in April 2026: period is this month, the preview is the
// invoice issued on 1 May 2026.
const MOCK_BILLING = {
  data: {
    result: {
      plan: "payg",
      period: "2026-04",
      invoice_period: "2026-05",
      issue_date: "2026-05-01",
      total: 125.5,
      line_items: [
        { description: "Storage (75 GB)", amount: 50 },
        { description: "AI Credits (3,500)", amount: 15 },
        { description: "Free tier savings", amount: -10 },
      ],
    },
  },
};

// The next invoice as billing-overview previews it in mid-November 2026:
// December's Boost fee and November's usage, less a credit, issued on
// 1 Dec 2026. period stays this month.
const MOCK_NEXT_INVOICE = {
  data: {
    result: {
      plan: "boost",
      period: "2026-11",
      invoice_period: "2026-12",
      issue_date: "2026-12-01",
      fee_period: "2026-12",
      usage_period: "2026-11",
      total: 151,
      line_items: [
        {
          line_type: "platform_fee",
          description: "Boost subscription (2026-12)",
          amount: 250,
        },
        {
          line_type: "usage",
          description: "AI Credits: 100.0 credits (2026-11)",
          amount: 1,
        },
        {
          line_type: "credit",
          description: "Credit: Goodwill credit",
          amount: -100,
        },
      ],
    },
  },
};

// A paid org with no usage this month and no plan fee next month: the
// preview has no lines and a total of 0, and the monthly run issues no
// invoice for it.
const MOCK_NOTHING_TO_INVOICE = {
  data: {
    result: {
      plan: "payg",
      period: "2026-11",
      invoice_period: "2026-12",
      issue_date: "2026-12-01",
      usage_period: "2026-11",
      total: 0,
      line_items: [],
    },
  },
};

// A credit covers the whole of December's fee: the total is 0, but the
// preview has lines, so the monthly run still issues a $0.00 invoice.
const MOCK_ZERO_WITH_LINES = {
  data: {
    result: {
      plan: "boost",
      period: "2026-11",
      invoice_period: "2026-12",
      issue_date: "2026-12-01",
      fee_period: "2026-12",
      usage_period: "2026-11",
      total: 0,
      line_items: [
        {
          line_type: "platform_fee",
          description: "Boost subscription (2026-12)",
          amount: 250,
        },
        {
          line_type: "credit",
          description: "Credit: Startup credit",
          amount: -250,
        },
      ],
    },
  },
};

// What an EE build without the next-invoice preview sends: no issue_date,
// and period is this month.
const MOCK_NO_ISSUE_DATE = {
  data: {
    result: {
      plan: "payg",
      period: "2026-11",
      total: 12.5,
      line_items: [
        {
          line_type: "usage",
          description: "AI Credits: 1,250.0 credits (2026-10)",
          amount: 12.5,
        },
      ],
    },
  },
};

// An add-on removal that takes effect on the issue date: the preview is
// priced without the ending add-on, and nothing is left to invoice.
const MOCK_REMOVAL_ON_ISSUE_DATE = {
  data: {
    result: {
      ...MOCK_NOTHING_TO_INVOICE.data.result,
      pending_cancel: true,
      cancel_at: "2026-12-01T00:00:00+00:00",
    },
  },
};

// The same removal taking effect mid-month, before the issue date.
const MOCK_REMOVAL_BEFORE_ISSUE_DATE = {
  data: {
    result: {
      ...MOCK_NOTHING_TO_INVOICE.data.result,
      pending_cancel: true,
      cancel_at: "2026-11-20T09:30:00+00:00",
    },
  },
};

// The same removal taking effect after the issue date.
const MOCK_REMOVAL_AFTER_ISSUE_DATE = {
  data: {
    result: {
      ...MOCK_NOTHING_TO_INVOICE.data.result,
      pending_cancel: true,
      cancel_at: "2027-01-01T00:00:00+00:00",
    },
  },
};

// A removal pending with no time given, which the preview prices as ending
// by the issue date.
const MOCK_REMOVAL_NO_TIME = {
  data: {
    result: {
      ...MOCK_NOTHING_TO_INVOICE.data.result,
      pending_cancel: true,
      cancel_at: null,
    },
  },
};

const REMOVAL_RACE_LINE =
  "If your change and this invoice fall at the same moment, the ending add-on may still be on this invoice.";

function withOverview(result) {
  const others = mockGet.getMockImplementation();
  mockGet.mockImplementation((url) =>
    url.includes("billing-overview") ? Promise.resolve(result) : others(url),
  );
}

const MOCK_INVOICES = {
  data: {
    result: {
      invoices: [
        {
          id: "inv-001",
          period_start: "2026-03-01",
          period_end: "2026-03-31",
          plan: "payg",
          total: 95.0,
          status: "paid",
          stripe_pdf_url: "https://stripe.com/pdf/inv-001",
        },
      ],
    },
  },
};

const MOCK_NOTIFICATIONS = {
  data: {
    result: {
      banners: [
        {
          id: "warn-ai_credits",
          type: "warning",
          message: "AI Credits at 85% of free tier",
          action: {
            label: "View details",
            url: "/settings/billing#ai_credits",
          },
        },
      ],
    },
  },
};

const MOCK_BUDGETS = {
  data: { result: { budgets: [] } },
};

const MOCK_PAYMENT_METHODS = {
  data: {
    result: [
      {
        id: "pm_123",
        brand: "Visa",
        last4: "4242",
        exp_month: 12,
        exp_year: 2028,
        is_default: true,
      },
    ],
  },
};

function renderWithQuery(ui) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("BillingPageV2", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url) => {
      if (url.includes("billing-overview"))
        return Promise.resolve(MOCK_BILLING);
      if (url.includes("invoices")) return Promise.resolve(MOCK_INVOICES);
      if (url.includes("notifications"))
        return Promise.resolve(MOCK_NOTIFICATIONS);
      if (url.includes("budgets")) return Promise.resolve(MOCK_BUDGETS);
      if (url.includes("payment-methods"))
        return Promise.resolve(MOCK_PAYMENT_METHODS);
      return Promise.resolve({ data: { result: {} } });
    });
  });

  it("should be importable", async () => {
    const module = await import("../BillingPage");
    expect(module.default).toBeDefined();
  });

  it("renders the next invoice preview with line items", async () => {
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    await waitFor(() => {
      expect(screen.getByText("Next invoice")).toBeInTheDocument();
    });

    expect(screen.getByText("Invoice date 1 May 2026")).toBeInTheDocument();
    expect(screen.getAllByText("$125.50").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Storage (75 GB)")).toBeInTheDocument();
  });

  it("names the next invoice and the day it is issued, and its Total is the server's total", async () => {
    withOverview(MOCK_NEXT_INVOICE);
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    expect(await screen.findByText("Next invoice")).toBeInTheDocument();
    // A future day, in words that do not read as past.
    expect(screen.getByText("Invoice date 1 Dec 2026")).toBeInTheDocument();
    expect(screen.queryByText(/^Issued \d/)).toBeNull();
    expect(screen.queryByText("Current period")).toBeNull();
    // Neither this month (period) nor the invoice's month is shown raw.
    expect(screen.queryByText("2026-11")).toBeNull();
    expect(screen.queryByText("2026-12")).toBeNull();

    const estimate = screen.getByText("Estimated total").parentElement;
    expect(within(estimate).getByText("$151.00")).toBeInTheDocument();
    const lines = screen
      .getByText("Boost subscription (2026-12)")
      .closest("table");
    const totalRow = within(lines).getByText("Total").closest("tr");
    expect(within(totalRow).getByText("$151.00")).toBeInTheDocument();

    // The fee and usage lines name their months; a credit line is a credit.
    expect(
      screen.getByText("AI Credits: 100.0 credits (2026-11)"),
    ).toBeInTheDocument();
    expect(screen.getByText("-$100.00")).toBeInTheDocument();
  });

  it("names no invoice date when the preview has no lines and a $0.00 total, since no invoice is issued", async () => {
    withOverview(MOCK_NOTHING_TO_INVOICE);
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    expect(await screen.findByText("Next invoice")).toBeInTheDocument();
    expect(screen.getByText("Nothing to invoice so far.")).toBeInTheDocument();
    expect(screen.queryByText(/^Invoice date/)).toBeNull();
    expect(screen.queryByText(/1 Dec 2026/)).toBeNull();
    expect(screen.queryByText(/^Issued \d/)).toBeNull();

    const estimate = screen.getByText("Estimated total").parentElement;
    expect(within(estimate).getByText("$0.00")).toBeInTheDocument();
  });

  it("keeps the invoice date when a $0.00 preview still has lines", async () => {
    withOverview(MOCK_ZERO_WITH_LINES);
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    expect(await screen.findByText("Next invoice")).toBeInTheDocument();
    expect(screen.getByText("Invoice date 1 Dec 2026")).toBeInTheDocument();
    expect(screen.queryByText("Nothing to invoice so far.")).toBeNull();
    expect(
      screen.getByText("Boost subscription (2026-12)"),
    ).toBeInTheDocument();
  });

  it("says the ending add-on may still be on the invoice when its removal falls on the issue date", async () => {
    withOverview(MOCK_REMOVAL_ON_ISSUE_DATE);
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    expect(await screen.findByText("Next invoice")).toBeInTheDocument();
    expect(screen.getByText("Nothing to invoice so far.")).toBeInTheDocument();
    const estimate = screen.getByText("Estimated total").parentElement;
    expect(within(estimate).getByText(REMOVAL_RACE_LINE)).toBeInTheDocument();
  });

  it("says nothing about the ending add-on when its removal takes effect before the issue date", async () => {
    withOverview(MOCK_REMOVAL_BEFORE_ISSUE_DATE);
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    expect(await screen.findByText("Next invoice")).toBeInTheDocument();
    expect(screen.queryByText(REMOVAL_RACE_LINE)).toBeNull();
  });

  it("says nothing about the ending add-on when its removal takes effect after the issue date", async () => {
    withOverview(MOCK_REMOVAL_AFTER_ISSUE_DATE);
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    expect(await screen.findByText("Next invoice")).toBeInTheDocument();
    expect(screen.queryByText(REMOVAL_RACE_LINE)).toBeNull();
  });

  it("says the ending add-on may still be on the invoice when a removal is pending with no time given", async () => {
    withOverview(MOCK_REMOVAL_NO_TIME);
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    expect(await screen.findByText("Next invoice")).toBeInTheDocument();
    const estimate = screen.getByText("Estimated total").parentElement;
    expect(within(estimate).getByText(REMOVAL_RACE_LINE)).toBeInTheDocument();
  });

  it("says nothing about an ending add-on when no removal is pending", async () => {
    withOverview(MOCK_NEXT_INVOICE);
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    expect(await screen.findByText("Next invoice")).toBeInTheDocument();
    expect(screen.queryByText(REMOVAL_RACE_LINE)).toBeNull();
  });

  it("keeps the Current period heading and this month when the server names no invoice date", async () => {
    withOverview(MOCK_NO_ISSUE_DATE);
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    expect(await screen.findByText("Current period")).toBeInTheDocument();
    expect(screen.getByText("2026-11")).toBeInTheDocument();
    expect(screen.queryByText("Next invoice")).toBeNull();
    expect(screen.queryByText(/^Invoice date/)).toBeNull();
    expect(screen.queryByText("Nothing to invoice so far.")).toBeNull();
  });

  it("renders alert banners from notifications API", async () => {
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    await waitFor(() => {
      expect(
        screen.getByText("AI Credits at 85% of free tier"),
      ).toBeInTheDocument();
    });
  });

  it("renders invoice history with expandable rows", async () => {
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    await waitFor(() => {
      expect(screen.getByText("Invoice history")).toBeInTheDocument();
    });

    // Invoice row visible
    expect(screen.getByText("2026-03-01 — 2026-03-31")).toBeInTheDocument();
    expect(screen.getByText("Paid")).toBeInTheDocument();
  });

  it("says No invoices when there are none", async () => {
    const overview = mockGet.getMockImplementation();
    mockGet.mockImplementation((url) =>
      url.includes("invoices")
        ? Promise.resolve({ data: { result: { invoices: [] } } })
        : overview(url),
    );
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    // Title and description render as separate elements; the empty state makes no forward promise
    // (credit-only and free accounts may never receive an end-of-period invoice).
    expect(await screen.findByText("No invoices")).toBeInTheDocument();
    expect(
      screen.getByText("Issued invoices are listed here."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/first invoice will appear/i)).toBeNull();
    expect(screen.queryByText(/No invoices yet/)).toBeNull();
  });

  it("renders payment methods section with card details", async () => {
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    await waitFor(() => {
      expect(screen.getByText("Payment methods")).toBeInTheDocument();
    });

    expect(screen.getByText("Visa •••• 4242")).toBeInTheDocument();
    expect(screen.getByText("Default")).toBeInTheDocument();
  });

  it("renders budget manager section", async () => {
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    await waitFor(() => {
      expect(screen.getByText("Usage budgets")).toBeInTheDocument();
    });

    expect(screen.getByText("Add Budget")).toBeInTheDocument();
  });

  it("shows free plan message when plan is free", async () => {
    mockGet.mockImplementation((url) => {
      if (url.includes("billing-overview"))
        return Promise.resolve({
          data: {
            result: {
              plan: "free",
              period: "2026-04",
              total: 0,
              line_items: [],
            },
          },
        });
      if (url.includes("notifications"))
        return Promise.resolve({ data: { result: { banners: [] } } });
      if (url.includes("budgets")) return Promise.resolve(MOCK_BUDGETS);
      if (url.includes("payment-methods"))
        return Promise.resolve({ data: { result: { paymentMethods: [] } } });
      return Promise.resolve({ data: { result: {} } });
    });

    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    await waitFor(() => {
      expect(screen.getByText(/Free plan/i)).toBeInTheDocument();
    });
  });

  describe("when /usage/v2 fails", () => {
    const notFound = () =>
      Promise.reject(
        Object.assign(new Error("Not found"), { statusCode: 404 }),
      );

    it("shows Billing couldn't be loaded with Retry, and no Free-plan default", async () => {
      let overviewCalls = 0;
      mockGet.mockImplementation((url) => {
        if (url.includes("billing-overview")) {
          overviewCalls += 1;
          return notFound();
        }
        return notFound();
      });

      const { default: BillingPageV2 } = await import("../BillingPage");
      renderWithQuery(<BillingPageV2 />);

      expect(
        await screen.findByText("Billing couldn't be loaded"),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          "Your plan, next invoice and invoice history are not shown until billing loads.",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText(/Free plan/i)).toBeNull();
      expect(screen.queryByText(/No billing this period/i)).toBeNull();
      expect(screen.queryByText(/No invoices yet/i)).toBeNull();
      expect(screen.queryByText(/No payment methods on file/i)).toBeNull();
      expect(screen.queryByText("$0.00")).toBeNull();

      const before = overviewCalls;
      screen.getByRole("button", { name: "Retry" }).click();
      await waitFor(() => expect(overviewCalls).toBe(before + 1));
    });

    it("shows the page once Retry succeeds", async () => {
      let fail = true;
      mockGet.mockImplementation((url) => {
        if (url.includes("billing-overview"))
          return fail ? notFound() : Promise.resolve(MOCK_BILLING);
        if (url.includes("invoices")) return Promise.resolve(MOCK_INVOICES);
        if (url.includes("notifications"))
          return Promise.resolve(MOCK_NOTIFICATIONS);
        if (url.includes("budgets")) return Promise.resolve(MOCK_BUDGETS);
        if (url.includes("payment-methods"))
          return Promise.resolve(MOCK_PAYMENT_METHODS);
        return notFound();
      });

      const { default: BillingPageV2 } = await import("../BillingPage");
      renderWithQuery(<BillingPageV2 />);
      await screen.findByText("Billing couldn't be loaded");
      fail = false;
      screen.getByRole("button", { name: "Retry" }).click();
      expect(await screen.findByText("Next invoice")).toBeInTheDocument();
      expect(screen.queryByText("Billing couldn't be loaded")).toBeNull();
    });

    it("shows a failed section in place of an empty one when only invoices, budgets and cards fail", async () => {
      mockGet.mockImplementation((url) => {
        if (url.includes("billing-overview"))
          return Promise.resolve(MOCK_BILLING);
        if (url.includes("notifications"))
          return Promise.resolve(MOCK_NOTIFICATIONS);
        return notFound();
      });

      const { default: BillingPageV2 } = await import("../BillingPage");
      renderWithQuery(<BillingPageV2 />);

      expect(
        await screen.findByText("Invoices couldn't be loaded"),
      ).toBeInTheDocument();
      expect(
        await screen.findByText("Usage budgets couldn't be loaded"),
      ).toBeInTheDocument();
      expect(
        await screen.findByText("Payment methods couldn't be loaded"),
      ).toBeInTheDocument();
      expect(screen.queryByText(/No invoices yet/i)).toBeNull();
      expect(screen.queryByText(/No budgets set/i)).toBeNull();
      expect(screen.queryByText(/No payment methods on file/i)).toBeNull();
    });
  });
});
