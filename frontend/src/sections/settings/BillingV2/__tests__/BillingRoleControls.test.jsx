import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import BudgetManager from "../BudgetManager";
import PaymentMethods from "../PaymentMethods";

// Production showed every budget and payment-method control to every member;
// changing them needs an organization Owner or Admin (org level 8 or more).
// A member keeps seeing each control, disabled, with the reason beside it.
const org = vi.hoisted(() => ({ level: 15 }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));

const api = vi.hoisted(() => ({ budgets: [], cards: [] }));
const mockPost = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();
vi.mock("src/utils/axios", () => ({
  default: {
    get: async (url) => {
      if (url === "/usage/v2/budgets/") {
        return { data: { result: { budgets: api.budgets } } };
      }
      if (url === "/usage/v2/payment-methods/") {
        return { data: { result: api.cards } };
      }
      throw new Error(`unmocked GET ${url}`);
    },
    post: (...args) => mockPost(...args),
    put: (...args) => mockPut(...args),
    delete: (...args) => mockDelete(...args),
  },
  endpoints: {
    settings: {
      v2: {
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
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const BUDGET_REASON = "Only an organization Owner or Admin can change budgets.";
const CARD_REASON =
  "Only an organization Owner or Admin can change payment methods.";

const BUDGET = {
  id: "b1",
  name: "Monthly AI credits",
  scope: "ai_credits",
  threshold_value: 100,
  action: "notify",
  last_triggered_period: null,
};
const CARDS = [
  {
    id: "pm_default",
    brand: "Visa",
    last4: "4242",
    exp_month: 12,
    exp_year: 2028,
    is_default: true,
  },
  {
    id: "pm_other",
    brand: "Mastercard",
    last4: "4444",
    exp_month: 1,
    exp_year: 2029,
    is_default: false,
  },
];

function renderWithQuery(ui) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  api.budgets = [];
  api.cards = [];
  mockPost.mockReset();
  mockPut.mockReset();
  mockDelete.mockReset();
});
afterEach(() => cleanup());

describe("Usage budgets controls by organization role", () => {
  it("shows a member every budget control, disabled, with the reason beside it", async () => {
    org.level = 3;
    api.budgets = [BUDGET];
    renderWithQuery(<BudgetManager />);
    await screen.findByText("Monthly AI credits");

    const add = screen.getByRole("button", { name: /add budget/i });
    expect(add).toBeDisabled();
    expect(add).toHaveAccessibleDescription(BUDGET_REASON);
    for (const name of ["Edit budget", "Delete budget"]) {
      const control = screen.getByRole("button", { name });
      expect(control).toBeDisabled();
      expect(control).toHaveAccessibleDescription(BUDGET_REASON);
    }
    expect(screen.getAllByText(BUDGET_REASON)).toHaveLength(1);
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("keeps the empty state's Add budget visible and disabled for a member", async () => {
    org.level = 1;
    renderWithQuery(<BudgetManager />);
    await screen.findByText("No budgets yet");

    const adds = screen.getAllByRole("button", { name: /add budget/i });
    expect(adds).toHaveLength(2);
    for (const add of adds) {
      expect(add).toBeDisabled();
      expect(add).toHaveAccessibleDescription(BUDGET_REASON);
    }
  });

  it("changes nothing for an organization Owner or Admin", async () => {
    org.level = 8;
    api.budgets = [BUDGET];
    renderWithQuery(<BudgetManager />);
    await screen.findByText("Monthly AI credits");

    for (const name of [/add budget/i, "Edit budget", "Delete budget"]) {
      expect(screen.getByRole("button", { name })).toBeEnabled();
    }
    expect(screen.queryByText(BUDGET_REASON)).toBeNull();
  });
});

describe("Payment methods controls by organization role", () => {
  it("shows a member every card control, disabled, with the reason beside it", async () => {
    org.level = 3;
    api.cards = CARDS;
    renderWithQuery(<PaymentMethods />);
    await screen.findByText(/4444/);

    const add = screen.getByRole("button", { name: /add card/i });
    expect(add).toBeDisabled();
    expect(add).toHaveAccessibleDescription(CARD_REASON);
    const setDefault = screen.getByRole("button", { name: "Set default" });
    expect(setDefault).toBeDisabled();
    expect(setDefault).toHaveAccessibleDescription(CARD_REASON);
    const removes = screen.getAllByRole("button", { name: "Remove card" });
    expect(removes).toHaveLength(2);
    for (const remove of removes) {
      expect(remove).toBeDisabled();
      expect(remove).toHaveAccessibleDescription(CARD_REASON);
    }
    expect(screen.getAllByText(CARD_REASON)).toHaveLength(1);
    expect(mockPost).not.toHaveBeenCalled();
    expect(mockDelete).not.toHaveBeenCalled();
  });

  it("keeps the empty state's Add card visible and disabled for a member", async () => {
    org.level = 1;
    renderWithQuery(<PaymentMethods />);
    await screen.findByText("No payment methods yet");

    const adds = screen.getAllByRole("button", { name: /add card/i });
    expect(adds).toHaveLength(2);
    for (const add of adds) {
      expect(add).toBeDisabled();
      expect(add).toHaveAccessibleDescription(CARD_REASON);
    }
  });

  it("changes nothing for an organization Owner or Admin", async () => {
    org.level = 15;
    api.cards = CARDS;
    renderWithQuery(<PaymentMethods />);
    await screen.findByText(/4444/);

    expect(screen.getByRole("button", { name: /add card/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Set default" })).toBeEnabled();
    for (const remove of screen.getAllByRole("button", {
      name: "Remove card",
    })) {
      expect(remove).toBeEnabled();
    }
    expect(screen.queryByText(CARD_REASON)).toBeNull();
  });
});

// Card rows are padded like the budget panels above, with no full black/white
// rule; multicolour brand logos sit on a light plate so the navy Visa logo
// shows on the dark theme, and single-colour icons keep the text colour.
describe("Payment methods layout", () => {
  it("pads each card row exactly like a budget panel", async () => {
    org.level = 15;
    api.budgets = [BUDGET];
    api.cards = CARDS;
    renderWithQuery(
      <>
        <BudgetManager />
        <PaymentMethods />
      </>,
    );
    await screen.findByText("Monthly AI credits");
    await screen.findByText(/4444/);

    const budgetRow = screen.getByTestId("usage-budget-row");
    const budgetPadding = getComputedStyle(budgetRow).padding;
    expect(budgetPadding).not.toBe("");
    const rows = screen.getAllByTestId("payment-method-row");
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(getComputedStyle(row).padding).toBe(budgetPadding);
    }
  });

  it("puts multicolour brand logos on a light plate and leaves single-colour icons alone", async () => {
    org.level = 15;
    api.cards = [
      ...CARDS,
      {
        id: "pm_amex",
        brand: "Amex",
        last4: "0005",
        exp_month: 3,
        exp_year: 2030,
        is_default: false,
      },
    ];
    renderWithQuery(<PaymentMethods />);
    await screen.findByText(/0005/);

    const plates = screen.getAllByTestId("card-brand-plate");
    // Visa and Mastercard draw their own colours; Amex draws in the text colour.
    expect(plates).toHaveLength(2);
    for (const plate of plates) {
      expect(plate).toHaveStyle({ backgroundColor: "rgb(255, 255, 255)" });
    }
  });
});
