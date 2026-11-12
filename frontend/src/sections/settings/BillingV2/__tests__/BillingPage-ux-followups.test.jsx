import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { shadows } from "src/theme/shadows";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";
import { BILLING_NO_ACCESS_MESSAGE } from "src/constants/usageCopy";
import PageHeader from "src/components/page-header";

import BillingPageV2 from "../BillingPage";
import BillingNoAccess from "../BillingNoAccess";

// Billing layout details, under the app theme (its table
// overrides draw the 1px line under every cell).

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
// the invoice table and the no-access view.
vi.mock("../BudgetManager", () => ({ default: () => null }));
vi.mock("../PaymentMethods", () => ({ default: () => null }));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// The real shared header, wrapped so a test can see that it drew the title.
vi.mock("src/components/page-header", async (importOriginal) => {
  const actual = await importOriginal();
  return { default: vi.fn(actual.default) };
});

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

const FIRST_PERIOD = "2026-10-01 — 2026-10-31";
const LAST_PERIOD = "2026-09-01 — 2026-09-30";
const NO_DETAILS = "No line item details available.";

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

async function invoiceRows() {
  const first = (await screen.findByText(FIRST_PERIOD)).closest("tr");
  const last = screen.getByText(LAST_PERIOD).closest("tr");
  return { first, last };
}

// The detail row that follows each invoice row holds one colSpan=6 cell.
function detailCell(row) {
  const cell = row.nextElementSibling.firstElementChild;
  expect(cell).toHaveAttribute("colspan", "6");
  return cell;
}

function lineWidths(cells) {
  return [...cells].map((cell) => getComputedStyle(cell).borderBottomWidth);
}

const KEPT = Array(6).fill("1px");
const CLEARED = Array(6).fill("0px");

describe("Billing UX follow-ups", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url) => {
      if (url.includes("billing-overview")) return Promise.resolve(BILLING);
      if (url.includes("/invoices/inv-"))
        return Promise.resolve({ data: { result: { line_items: [] } } });
      if (url.includes("invoices")) return Promise.resolve(INVOICES);
      if (url.includes("notifications"))
        return Promise.resolve({ data: { result: { banners: [] } } });
      return Promise.resolve({ data: { result: {} } });
    });
  });

  // Review note P3-1: with the empty band gone, the last invoice row's cell
  // line sat on the outlined Paper's own bottom border, a doubled edge.
  it.each(["light", "dark"])(
    "clears the line under the last collapsed invoice, where the table meets its outline (%s)",
    async (mode) => {
      renderBilling(<BillingPageV2 />, mode);
      const { first, last } = await invoiceRows();

      // The last invoice's detail row ends the table body.
      expect(last.nextElementSibling.nextElementSibling).toBeNull();
      expect(last.children).toHaveLength(6);
      expect(lineWidths(last.children)).toEqual(CLEARED);
      expect(lineWidths(first.children)).toEqual(KEPT);
      // Collapsed detail cells keep no line of their own.
      expect(lineWidths([detailCell(first), detailCell(last)])).toEqual([
        "0px",
        "0px",
      ]);
    },
  );

  it("keeps the line between the last invoice and its open details, and clears the one under them", async () => {
    renderBilling(<BillingPageV2 />);
    const { first, last } = await invoiceRows();

    fireEvent.click(last);
    expect(await screen.findByText(NO_DETAILS)).toBeInTheDocument();

    expect(lineWidths(last.children)).toEqual(KEPT);
    expect(getComputedStyle(detailCell(last)).borderBottomWidth).toBe("0px");
    expect(lineWidths(first.children)).toEqual(KEPT);
  });

  it("keeps the line under an open invoice that is not the last", async () => {
    renderBilling(<BillingPageV2 />);
    const { first } = await invoiceRows();

    fireEvent.click(first);
    expect(await screen.findByText(NO_DETAILS)).toBeInTheDocument();

    expect(lineWidths(first.children)).toEqual(KEPT);
    expect(getComputedStyle(detailCell(first)).borderBottomWidth).toBe("1px");
  });

  // Review note P3-4: the no-access view hand-rolled its title instead of
  // using the shared page header every other Settings page uses.
  it("titles the no-access Billing view with the shared page header and changes nothing else", () => {
    const { container } = renderBilling(<BillingNoAccess />);

    expect(PageHeader).toHaveBeenCalled();
    expect(PageHeader.mock.lastCall[0]).toMatchObject({
      title: "Billing",
      description: BILLING_NO_ACCESS_MESSAGE,
    });

    const heading = screen.getByRole("heading", { level: 1, name: "Billing" });
    expect(screen.getAllByRole("heading")).toEqual([heading]);
    expect(screen.getByText(BILLING_NO_ACCESS_MESSAGE)).toBeInTheDocument();
    expect(container.textContent).toBe(`Billing${BILLING_NO_ACCESS_MESSAGE}`);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });
});
