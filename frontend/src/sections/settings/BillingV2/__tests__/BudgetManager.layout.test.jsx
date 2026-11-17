import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import BudgetManager from "../BudgetManager";

const state = vi.hoisted(() => ({ get: vi.fn() }));

// An organization Owner, so the budget dialogs open.
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
    delete: vi.fn(),
  },
  endpoints: {
    settings: {
      v2: {
        budgets: "/usage/v2/budgets/",
        budgetDetail: (id) => `/usage/v2/budgets/${id}/`,
      },
    },
  },
}));

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <BudgetManager />
    </QueryClientProvider>,
  );
}

// The helper line MUI renders under a field, if any.
const helperLine = (input) =>
  input
    .closest(".MuiFormControl-root")
    .querySelector(".MuiFormHelperText-root");

beforeEach(() => {
  state.get.mockReset();
  state.get.mockResolvedValue({
    data: {
      result: {
        budgets: [
          {
            id: "budget-1",
            name: "Monthly AI credits cap",
            scope: "ai_credits",
            threshold_value: "500",
            action: "notify",
          },
        ],
      },
    },
  });
});

// A blank helper line (helperText " ") was always
// reserved under Threshold, leaving a ~40 px gap above Action.
describe("Budget dialog Threshold field", () => {
  it("reserves no blank helper line under an empty or valid Threshold in Add Usage Budget", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Add Budget" }));
    const threshold = screen.getByLabelText("Threshold");
    expect(helperLine(threshold)).toBeNull();
    expect(threshold).not.toHaveAttribute("aria-describedby");

    fireEvent.change(threshold, { target: { value: "5000" } });
    expect(helperLine(threshold)).toBeNull();
    expect(threshold).not.toHaveAttribute("aria-describedby");
  });

  it("still shows the error under an invalid Threshold, and removes it once valid", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Add Budget" }));
    const threshold = screen.getByLabelText("Threshold");

    fireEvent.change(threshold, { target: { value: "1e5" } });
    expect(helperLine(threshold)).toHaveTextContent("Enter a positive number");
    expect(threshold).toHaveAccessibleDescription("Enter a positive number");
    expect(threshold).toHaveAttribute("aria-invalid", "true");

    fireEvent.change(threshold, { target: { value: "12.5" } });
    expect(helperLine(threshold)).toBeNull();
    expect(threshold).toHaveAttribute("aria-invalid", "false");
  });

  it("reserves no blank helper line under a saved Threshold in Edit Budget", async () => {
    mount();
    await waitFor(() =>
      expect(screen.getByTitle("Edit budget")).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByTitle("Edit budget"));
    expect(
      await screen.findByRole("dialog", { name: "Edit Budget" }),
    ).toBeVisible();
    const threshold = screen.getByLabelText("Threshold");
    expect(threshold).toHaveValue("500");
    expect(helperLine(threshold)).toBeNull();
    expect(threshold).not.toHaveAttribute("aria-describedby");
  });
});
