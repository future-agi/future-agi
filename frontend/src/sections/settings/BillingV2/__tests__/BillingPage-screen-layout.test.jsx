import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { shadows } from "src/theme/shadows";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { ROW_HEIGHT } from "src/theme/sizes";
import { componentsOverrides } from "src/theme/overrides";

import BillingPageV2 from "../BillingPage";
import BillingNoAccess from "../BillingNoAccess";

// Screen-pass layout checks for Settings > Billing, under the app theme (its
// component overrides set the table row heights the test-utils theme lacks).

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

// Budgets and payment methods have their own tests; these checks are about
// the page wrapper and the invoice table.
vi.mock("../BudgetManager", () => ({ default: () => null }));
vi.mock("../PaymentMethods", () => ({ default: () => null }));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

const BILLING = {
  data: {
    result: {
      plan: "payg",
      period: "Nov 1 - Nov 30, 2026",
      total: 128.4,
      line_items: [{ description: "Platform fee", amount: 50 }],
    },
  },
};

const INVOICES = {
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
        },
        {
          id: "inv-sep",
          period_start: "2026-09-01",
          period_end: "2026-09-30",
          plan: "payg",
          total: 98.1,
          status: "paid",
        },
      ],
    },
  },
};

const SIDES = ["Top", "Right", "Bottom", "Left"];

function appTheme(mode) {
  const theme = createTheme({
    palette: palette(mode),
    customShadows: customShadows(mode),
    shadows: shadows(mode),
    shape: dsShape,
    typography,
  });
  theme.components = componentsOverrides(theme);
  return theme;
}

function renderBilling(ui, mode = "light") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>, {
    theme: appTheme(mode),
  });
}

function paddings(element) {
  const style = getComputedStyle(element);
  return SIDES.map((side) => style[`padding${side}`]);
}

describe("Billing screen layout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url) => {
      if (url.includes("billing-overview")) return Promise.resolve(BILLING);
      if (url.includes("invoices")) return Promise.resolve(INVOICES);
      if (url.includes("notifications"))
        return Promise.resolve({ data: { result: { banners: [] } } });
      return Promise.resolve({ data: { result: {} } });
    });
  });

  // Finding rank 3: every invoice row was followed by an empty band the
  // height of a compact table row, from the collapsed detail row's cell.
  it.each(["light", "dark"])(
    "gives a collapsed invoice's detail row no height of its own (%s)",
    async (mode) => {
      renderBilling(<BillingPageV2 />, mode);

      expect(
        await screen.findByText("2026-10-01 — 2026-10-31"),
      ).toBeInTheDocument();
      expect(screen.getByText("2026-09-01 — 2026-09-30")).toBeInTheDocument();

      const detailCells = document.querySelectorAll('td[colspan="6"]');
      expect(detailCells).toHaveLength(2);
      detailCells.forEach((cell) => {
        expect(cell).toBeEmptyDOMElement();
        const { height } = getComputedStyle(cell);
        expect(height).not.toBe(`${ROW_HEIGHT.compact}px`);
        expect(height).toBe("auto");
      });
    },
  );

  // Finding rank 4: the no-access view had its own 24px inset on top of the
  // settings shell padding, so it started right of and below every other
  // Settings page.
  it("starts the no-access Billing view where the Billing page starts", async () => {
    const loaded = renderBilling(<BillingPageV2 />);
    expect(await screen.findByText("Invoice history")).toBeInTheDocument();
    const pagePadding = paddings(loaded.container.firstChild);
    loaded.unmount();

    const { container } = renderBilling(<BillingNoAccess />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Billing" }),
    ).toBeInTheDocument();
    expect(paddings(container.firstChild)).toEqual(pagePadding);
  });
});
