import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import BudgetManager from "../BudgetManager";
import PaymentMethods from "../PaymentMethods";

const state = vi.hoisted(() => ({
  orgLevel: 15,
  workspaceRole: "workspace_admin",
  threshold: "0.000001",
  scope: "total_spend",
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  remove: vi.fn(),
}));

// Exercise the real useGatewayAdmin hook. Workspace authority and the legacy
// home-org role must not override membership in the selected organization.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: state.orgLevel }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    role: state.workspaceRole,
    user: { organization_role: "Owner" },
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => state.get(...args),
    post: (...args) => state.post(...args),
    put: (...args) => state.put(...args),
    delete: (...args) => state.remove(...args),
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

function mount(ui) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrap = (child) => (
    <QueryClientProvider client={client}>{child}</QueryClientProvider>
  );
  const result = render(wrap(ui));
  return { ...result, rerender: (child) => result.rerender(wrap(child)) };
}

function expectNoWrites() {
  expect(state.post).not.toHaveBeenCalled();
  expect(state.put).not.toHaveBeenCalled();
  expect(state.remove).not.toHaveBeenCalled();
}

beforeEach(() => {
  vi.clearAllMocks();
  state.orgLevel = 15;
  state.workspaceRole = "workspace_admin";
  state.threshold = "0.000001";
  state.scope = "total_spend";
  state.get.mockImplementation(async (url) => ({
    data: {
      result: url.includes("budgets")
        ? {
            budgets: [
              {
                id: "budget-1",
                name: "Spend ceiling",
                scope: state.scope,
                threshold_value: state.threshold,
                action: "notify",
              },
            ],
          }
        : [
            {
              id: "pm_test",
              brand: "Visa",
              last4: "4242",
              exp_month: 12,
              exp_year: 2028,
              is_default: false,
            },
          ],
    },
  }));
  state.post.mockResolvedValue({ data: { result: {} } });
  state.put.mockResolvedValue({ data: { result: {} } });
  state.remove.mockResolvedValue({ data: { result: {} } });
  window.history.replaceState({}, "", "/dashboard/settings/billing");
});

afterEach(() => {
  window.history.replaceState({}, "", "/");
});

// A total-spend threshold is in US dollars: budget enforcement compares it
// with the period's invoice total.
describe("total-spend thresholds keep the exact decimal text", () => {
  it.each([
    ["0.000001", "$0.000001"],
    ["0.0000001234567890123456789", "$0.0000001234567890123456789"],
    ["9007199254740993.000001", "$9,007,199,254,740,993.000001"],
  ])(
    "renders %s as grouped dollars without Number or locale rounding",
    async (value, text) => {
      state.threshold = value;
      mount(<BudgetManager />);
      expect(
        await screen.findByText(`Total Spend: ${text} → Notify`),
      ).toBeVisible();
    },
  );

  it("leaves non-money thresholds as counts, with their unit", async () => {
    state.scope = "gateway_requests";
    state.threshold = "5";
    mount(<BudgetManager />);
    expect(
      await screen.findByText("Gateway Requests: 5 requests → Notify"),
    ).toBeVisible();
  });
});

const readers = [
  ["workspace admin, org member", 3, "workspace_admin"],
  ["member", 3, "workspace_member"],
  ["viewer", 1, "workspace_viewer"],
  ["unknown", null, "workspace_admin"],
  ["untrusted string level", "8", "workspace_admin"],
];

describe.each(readers)(
  "billing read-only for %s",
  (_label, level, workspaceRole) => {
    beforeEach(() => {
      state.orgLevel = level;
      state.workspaceRole = workspaceRole;
    });

    it("shows budgets with every control visible and disabled, with the reason", async () => {
      mount(<BudgetManager />);
      await screen.findByText("Spend ceiling");
      const reason = "Only an organization Owner or Admin can change budgets.";
      expect(screen.getByText(reason)).toBeVisible();
      for (const control of [
        screen.getByRole("button", { name: "Add Budget" }),
        screen.getByTitle("Edit budget"),
        screen.getByTitle("Delete budget"),
      ]) {
        expect(control).toBeDisabled();
        expect(control).toHaveAccessibleDescription(reason);
        fireEvent.click(control);
      }
      expect(screen.queryByRole("dialog")).toBeNull();
      expectNoWrites();
    });

    it("shows cards with every control visible and disabled, and blocks checkout-return PUT", async () => {
      window.history.replaceState(
        {},
        "",
        "?card=added&session_id=cs_test&keep=1",
      );
      mount(<PaymentMethods />);
      await screen.findByText("Visa •••• 4242");
      const reason =
        "Only an organization Owner or Admin can change payment methods.";
      expect(screen.getByText(reason)).toBeVisible();
      for (const control of [
        screen.getByRole("button", { name: "Add Card" }),
        screen.getByRole("button", { name: "Set default" }),
        screen.getByTitle("Remove card"),
      ]) {
        expect(control).toBeDisabled();
        expect(control).toHaveAccessibleDescription(reason);
        fireEvent.click(control);
      }
      expect(screen.queryByRole("dialog")).toBeNull();
      expectNoWrites();
    });
  },
);

describe.each([
  ["Owner", 15],
  ["Admin", 8],
])("billing writes for org %s", (_label, level) => {
  beforeEach(() => {
    state.orgLevel = level;
    state.workspaceRole = "workspace_member";
  });

  it("can create, edit and delete a budget with exact input text", async () => {
    mount(<BudgetManager />);
    fireEvent.click(await screen.findByRole("button", { name: "Add Budget" }));
    fireEvent.change(screen.getByLabelText("Budget Name"), {
      target: { value: "New ceiling" },
    });
    fireEvent.change(screen.getByLabelText("Threshold"), {
      target: { value: "0.000001" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create Budget" }));
    await waitFor(() =>
      expect(state.post).toHaveBeenCalledWith(
        "/usage/v2/budgets/",
        expect.objectContaining({ threshold_value: "0.000001" }),
      ),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    fireEvent.click(screen.getByTitle("Edit budget"));
    fireEvent.change(screen.getByLabelText("Threshold"), {
      target: { value: "0.000002" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    await waitFor(() =>
      expect(state.put).toHaveBeenCalledWith(
        "/usage/v2/budgets/budget-1/",
        expect.objectContaining({ threshold_value: "0.000002" }),
      ),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    fireEvent.click(screen.getByTitle("Delete budget"));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(state.remove).toHaveBeenCalledWith("/usage/v2/budgets/budget-1/"),
    );
  });

  it("can set default, remove a card and request setup", async () => {
    mount(<PaymentMethods />);
    fireEvent.click(await screen.findByRole("button", { name: "Set default" }));
    await waitFor(() =>
      expect(state.post).toHaveBeenCalledWith(
        "/usage/v2/payment-methods/pm_test/default/",
      ),
    );
    fireEvent.click(screen.getByTitle("Remove card"));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() =>
      expect(state.remove).toHaveBeenCalledWith(
        "/usage/v2/payment-methods/pm_test/",
      ),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Add Card" }));
    // Refuse the mocked setup, avoiding browser navigation in the test.
    state.post.mockRejectedValueOnce(new Error("setup unavailable"));
    fireEvent.click(screen.getAllByRole("button", { name: "Add Card" }).at(-1));
    await waitFor(() =>
      expect(state.post).toHaveBeenCalledWith(
        "/usage/v2/payment-methods/setup-intent/",
      ),
    );
  });

  it("confirms a checkout return once", async () => {
    // An accepted confirmation (status + payment_method_id) clears the return parameters;
    // an unaccepted one keeps them so a reload can retry (W02-003).
    state.put.mockResolvedValue({
      data: { status: true, result: { payment_method_id: "pm_test" } },
    });
    window.history.replaceState(
      {},
      "",
      "?card=added&session_id=cs_test&keep=1",
    );
    const view = mount(<PaymentMethods />);
    await waitFor(() =>
      expect(state.put).toHaveBeenCalledWith("/usage/v2/payment-methods/", {
        session_id: "cs_test",
      }),
    );
    view.rerender(<PaymentMethods />);
    await waitFor(() => expect(window.location.search).toBe("?keep=1"));
    expect(state.put).toHaveBeenCalledTimes(1);
  });
});

it.each([
  ["budget edit", BudgetManager, "Edit budget"],
  ["budget delete", BudgetManager, "Delete budget"],
  ["card delete", PaymentMethods, "Remove card"],
])(
  "closes the %s dialog on an org-role downgrade",
  async (_label, Component, title) => {
    const view = mount(<Component />);
    fireEvent.click(await screen.findByTitle(title));
    expect(screen.getByRole("dialog")).toBeVisible();
    state.orgLevel = 3;
    view.rerender(<Component />);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expectNoWrites();
  },
);

it("waits for selected-org authority before confirming a checkout return", async () => {
  state.orgLevel = null;
  window.history.replaceState({}, "", "?card=added&session_id=cs_test");
  const view = mount(<PaymentMethods />);
  await screen.findByText("Visa •••• 4242");
  expectNoWrites();
  state.orgLevel = 8;
  view.rerender(<PaymentMethods />);
  await waitFor(() => expect(state.put).toHaveBeenCalledTimes(1));
});
