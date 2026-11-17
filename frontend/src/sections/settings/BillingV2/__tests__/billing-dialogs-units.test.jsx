import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Button, DialogActions, DialogContent } from "@mui/material";
import { createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { ClosableDialog } from "src/components/custom-dialog";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { shadows } from "src/theme/shadows";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";

import BudgetManager from "../BudgetManager";
import PaymentMethods from "../PaymentMethods";

// The Billing budgets, cards and their dialogs: Remove Payment Method?,
// Delete Budget? and Add Payment Method keep 24 px above their buttons, Add
// Usage Budget has a close button, card brands read as names, and budget
// rows show their dimension's unit and a readable month; a total-spend row
// and its Threshold field are in US dollars. Remove Payment Method? and
// Delete Budget? keep naming their card or budget until they have faded out.

const state = vi.hoisted(() => ({ get: vi.fn(), del: vi.fn() }));

// An organization Owner, so every budget and card dialog opens.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    role: "workspace_admin",
    user: { organization_role: "Owner" },
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => state.get(...args),
    post: vi.fn(),
    put: vi.fn(),
    delete: (...args) => state.del(...args),
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

// An owner account. Stripe sends card brands in lowercase.
const BUDGETS = [
  {
    id: "budget-ai",
    name: "Monthly AI credits cap",
    scope: "ai_credits",
    threshold_value: "500.0000",
    action: "notify",
  },
  {
    id: "budget-spend",
    name: "Total spend hard stop",
    scope: "total_spend",
    threshold_value: "2500.0000",
    action: "pause",
    last_triggered_period: "2026-10",
  },
];

const CARDS = [
  {
    id: "pm_visa",
    brand: "visa",
    last4: "4242",
    exp_month: 12,
    exp_year: 2028,
    is_default: true,
  },
  {
    id: "pm_mc",
    brand: "mastercard",
    last4: "4444",
    exp_month: 3,
    exp_year: 2027,
    is_default: false,
  },
];

// The app theme (8 px spacing and the dialog overrides); the test-utils
// theme spaces in rem.
function appTheme(mode = "light") {
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

function mount(ui) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
    {
      theme: appTheme(),
    },
  );
}

const px = (value) => parseFloat(value) || 0;

// The space between a dialog's message and its buttons: the bottom padding
// and margin of the message and of every box around it, up to the box that
// also holds the buttons, plus the buttons' own top margin and top padding.
function gapAboveButtons(message) {
  const dialog = screen.getByRole("dialog");
  const actions = within(dialog)
    .getByRole("button", { name: "Cancel" })
    .closest(".MuiDialogActions-root");
  const actionsStyle = getComputedStyle(actions);
  let gap = px(actionsStyle.marginTop) + px(actionsStyle.paddingTop);
  for (let el = message; !el.contains(actions); el = el.parentElement) {
    const style = getComputedStyle(el);
    gap += px(style.paddingBottom) + px(style.marginBottom);
  }
  return gap;
}

// Lets a request that is under way finish and renders what follows it.
const settle = () =>
  act(() => new Promise((resolve) => setTimeout(resolve, 0)));

beforeEach(() => {
  vi.clearAllMocks();
  state.get.mockImplementation(async (url) => ({
    data: {
      result: url.includes("budgets") ? { budgets: BUDGETS } : CARDS,
    },
  }));
  state.del.mockResolvedValue({ data: { status: true } });
});

// Design system ConfirmDialog: 24 px (marginTop: 3) between message and buttons.
describe("Billing confirm dialogs keep 24 px above their buttons", () => {
  it("Remove Payment Method?", async () => {
    mount(<PaymentMethods />);
    await screen.findByText(/4242/);
    fireEvent.click(screen.getAllByTitle("Remove card")[0]);

    expect(gapAboveButtons(screen.getByText(/This cannot be undone/))).toBe(24);
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByRole("button", { name: "Remove" }),
    ).toBeVisible();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeVisible();
  });

  it("Delete Budget?", async () => {
    mount(<BudgetManager />);
    await screen.findByText("Monthly AI credits cap");
    fireEvent.click(screen.getAllByTitle("Delete budget")[0]);

    expect(gapAboveButtons(screen.getByText(/remove the budget rule/))).toBe(
      24,
    );
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByRole("button", { name: "Delete" }),
    ).toBeVisible();
  });

  it("Add Payment Method", async () => {
    mount(<PaymentMethods />);
    await screen.findByText(/4242/);
    fireEvent.click(screen.getByRole("button", { name: "Add Card" }));

    expect(gapAboveButtons(screen.getByText(/redirected to Stripe/))).toBe(24);
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByRole("button", { name: "Add Card" }),
    ).toBeVisible();
  });

  it("counts top padding on the buttons row as part of the gap", () => {
    mount(
      <ClosableDialog open onClose={() => {}}>
        <DialogContent sx={{ m: 0, p: 0 }}>Padded message</DialogContent>
        <DialogActions sx={{ m: 0, p: 0, pt: 3 }}>
          <Button>Cancel</Button>
        </DialogActions>
      </ClosableDialog>,
    );

    expect(gapAboveButtons(screen.getByText("Padded message"))).toBe(24);
  });
});

describe("Remove and Delete dialogs keep naming their target while they close", () => {
  const REMOVE_VISA = "Remove Visa •••• 4242? This cannot be undone.";
  const deleteText = (name) =>
    `Delete "${name}"? This will remove the budget rule and clear any pause flags.`;

  it("still names the card after Cancel, and names the next card it opens for", async () => {
    mount(<PaymentMethods />);
    await screen.findByText("Visa •••• 4242");
    fireEvent.click(screen.getAllByTitle("Remove card")[0]);
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Cancel",
      }),
    );

    expect(screen.getByText(REMOVE_VISA)).toBeInTheDocument();
    expect(screen.queryByText(/Remove Card ••••/)).toBeNull();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getAllByTitle("Remove card")[1]);
    expect(
      within(screen.getByRole("dialog")).getByText(
        "Remove Mastercard •••• 4444? This cannot be undone.",
      ),
    ).toBeInTheDocument();
  });

  it("still names the card after the card is removed, until the dialog has gone", async () => {
    mount(<PaymentMethods />);
    await screen.findByText("Visa •••• 4242");
    fireEvent.click(screen.getAllByTitle("Remove card")[0]);
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Remove",
      }),
    );
    await settle();

    expect(state.del).toHaveBeenCalledWith(
      "/usage/v2/payment-methods/pm_visa/",
    );
    expect(enqueueSnackbar).toHaveBeenCalledWith("Payment method removed", {
      variant: "success",
    });
    expect(screen.getByText(REMOVE_VISA)).toBeInTheDocument();
    expect(screen.queryByText(/Remove Card ••••/)).toBeNull();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("still names the budget after Cancel, and names the next budget it opens for", async () => {
    mount(<BudgetManager />);
    await screen.findByText("Monthly AI credits cap");
    fireEvent.click(screen.getAllByTitle("Delete budget")[0]);
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Cancel",
      }),
    );

    expect(
      screen.getByText(deleteText("Monthly AI credits cap")),
    ).toBeInTheDocument();
    expect(screen.queryByText(deleteText(""))).toBeNull();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getAllByTitle("Delete budget")[1]);
    expect(
      within(screen.getByRole("dialog")).getByText(
        deleteText("Total spend hard stop"),
      ),
    ).toBeInTheDocument();
  });

  it("still names the budget after the budget is deleted, until the dialog has gone", async () => {
    mount(<BudgetManager />);
    await screen.findByText("Monthly AI credits cap");
    fireEvent.click(screen.getAllByTitle("Delete budget")[0]);
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Delete",
      }),
    );
    await settle();

    expect(state.del).toHaveBeenCalledWith("/usage/v2/budgets/budget-ai/");
    expect(enqueueSnackbar).toHaveBeenCalledWith("Budget deleted", {
      variant: "success",
    });
    expect(
      screen.getByText(deleteText("Monthly AI credits cap")),
    ).toBeInTheDocument();
    expect(screen.queryByText(deleteText(""))).toBeNull();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

describe("Add Usage Budget has a close button", () => {
  it("closes from the × and keeps Cancel and Create Budget", async () => {
    mount(<BudgetManager />);
    fireEvent.click(await screen.findByRole("button", { name: "Add Budget" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Add Usage Budget",
    });
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeVisible();
    expect(
      within(dialog).getByRole("button", { name: "Create Budget" }),
    ).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

describe("Card brands read as names", () => {
  it("capitalises the brand on each card and in the Remove dialog", async () => {
    mount(<PaymentMethods />);
    expect(await screen.findByText("Visa •••• 4242")).toBeInTheDocument();
    expect(screen.getByText("Mastercard •••• 4444")).toBeInTheDocument();
    expect(screen.queryByText(/visa ••••|mastercard ••••/)).toBeNull();

    fireEvent.click(screen.getAllByTitle("Remove card")[0]);
    expect(
      within(screen.getByRole("dialog")).getByText(
        "Remove Visa •••• 4242? This cannot be undone.",
      ),
    ).toBeInTheDocument();
  });
});

describe("Usage budgets say their unit", () => {
  it("shows each dimension row's amount with its unit and a named month, and a total-spend row in grouped dollars", async () => {
    mount(<BudgetManager />);
    expect(
      await screen.findByText("AI Credits: 500 credits → Notify"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Total Spend: $2,500.00 → Pause Usage"),
    ).toBeInTheDocument();
    expect(screen.getByText("Triggered Oct 2026")).toBeInTheDocument();
    expect(screen.queryByText(/2026-10/)).toBeNull();
  });

  it("shows the unit inside the Threshold field, credits for AI Credits", async () => {
    mount(<BudgetManager />);
    fireEvent.click(await screen.findByRole("button", { name: "Add Budget" }));
    const threshold = screen.getByLabelText("Threshold");
    expect(threshold.closest(".MuiInputBase-root")).toHaveTextContent(
      "credits",
    );
  });

  it("shows USD inside the Threshold field of a total-spend budget", async () => {
    mount(<BudgetManager />);
    await screen.findByText("Total spend hard stop");
    fireEvent.click(screen.getAllByTitle("Edit budget")[1]);
    expect(
      await screen.findByRole("dialog", { name: "Edit Budget" }),
    ).toBeVisible();
    const threshold = screen.getByLabelText("Threshold");
    expect(threshold).toHaveValue("2500.0000");
    expect(threshold.closest(".MuiInputBase-root")).toHaveTextContent("USD");
  });
});
