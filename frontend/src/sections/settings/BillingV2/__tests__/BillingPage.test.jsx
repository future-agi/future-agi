import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
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

const MOCK_BILLING = {
  data: {
    result: {
      plan: "payg",
      period: "2026-04",
      total: 125.5,
      line_items: [
        { description: "Storage (75 GB)", amount: 50 },
        { description: "AI Credits (3,500)", amount: 15 },
        { description: "Free tier savings", amount: -10 },
      ],
    },
  },
};

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

  it("renders current period summary with line items", async () => {
    const { default: BillingPageV2 } = await import("../BillingPage");
    renderWithQuery(<BillingPageV2 />);

    await waitFor(() => {
      expect(screen.getByText("Current period")).toBeInTheDocument();
    });

    expect(screen.getAllByText("$125.50").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Storage (75 GB)")).toBeInTheDocument();
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
      expect(await screen.findByText("Current period")).toBeInTheDocument();
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
