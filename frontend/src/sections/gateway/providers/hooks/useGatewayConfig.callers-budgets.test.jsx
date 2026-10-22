// The budget callers of useGatewayConfig's writes on a
// save the gateway did not apply (gateway_synced:false). Each shows exactly
// one toast, the warning, never its success toast and never its generic
// "Failed to ..." toast.
//
//   budgets/SetBudgetDialog.jsx     Set Budget (useSetBudget)
//   budgets/BudgetRBACSection.jsx   remove budget (useRemoveBudget)
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import SetBudgetDialog from "../../budgets/SetBudgetDialog";
import BudgetRBACSection from "../../budgets/BudgetRBACSection";

// Budget, alerting, routing and fallback writes are Owner or Admin only
// (useGatewayAdmin); these cases act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const WARNING =
  "Config saved but gateway sync failed. Changes will apply on next gateway restart.";
const SENTENCE = `Saved, but the gateway did not apply it: ${WARNING}`;

const { post, get, snackbars } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  snackbars: [],
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

vi.mock("src/utils/axios", () => {
  const p = (name) => (id) => `/agentcc/gateways/${id}/${name}/`;
  return {
    default: { post, get },
    endpoints: {
      gateway: {
        config: p("config"),
        setBudget: p("set-budget"),
        removeBudget: p("remove-budget"),
        requestLogs: "/agentcc/request-logs/",
      },
    },
  };
});

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1", isLoading: false }),
}));
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: null }),
}));

const CONFIG = { budgets: { hard_limit: { limit: 1000, on_exceed: "block" } } };

function renderWithClient(ui) {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

const expectOnlyTheWarning = () =>
  expect(snackbars).toEqual([
    { message: SENTENCE, options: { variant: "warning" } },
  ]);

beforeEach(() => {
  post.mockReset();
  get.mockReset();
  snackbars.length = 0;
  get.mockImplementation(async (url) =>
    url.endsWith("/config/")
      ? { data: { status: true, result: CONFIG } }
      : { data: { status: true, result: [] } },
  );
  post.mockResolvedValue({
    data: {
      status: true,
      result: { gateway_synced: false, gateway_warning: WARNING },
    },
  });
});

describe("budget callers on gateway_synced:false", () => {
  it("Set Budget: the warning and the dialog's Alert, no 'saved' toast", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithClient(
      <SetBudgetDialog open onClose={onClose} gatewayId="gw-1" />,
    );
    await user.click(screen.getByLabelText(/Budget Level/));
    await user.click(screen.getByRole("option", { name: "Organization" }));
    await user.type(screen.getByLabelText(/Limit \(\$\)/), "250");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(post.mock.calls[0][0]).toBe("/agentcc/gateways/gw-1/set-budget/");
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Remove budget: the warning, no 'removed' and no 'Failed to remove' toast", async () => {
    const user = userEvent.setup();
    renderWithClient(<BudgetRBACSection />);
    await user.click(await screen.findByTitle("Remove budget"));

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(post.mock.calls[0][0]).toBe("/agentcc/gateways/gw-1/remove-budget/");
  });
});
