import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import UsageSummary from "src/pages/dashboard/settings/UsageSummary/UsageSummary";
import CreditBalanceChip from "src/sections/gateway/providers/ui/CreditBalanceChip";
import { isGatewayControlHidden, HIDDEN_GATEWAY_CONTROLS } from "src/sections/gateway/shell/page-status";

import BillingPageV2 from "../BillingPage";
import { useCentralBilling } from "../useCentralBilling";

// This backend serves no managed-credit ledger (/agentcc/managed-credits/*),
// so the control is hidden (page-status.js HIDDEN_GATEWAY_CONTROLS
// .managedCredits): no page asks for the ledger, and nothing about gateway
// credits is shown, on cloud or off it.

const state = vi.hoisted(() => ({ isCloud: false, calls: [] }));

// Keep the unsupported/deployment-hide case as an explicit hypothetical,
// while the real product no longer globally hides its served ledger.
vi.mock("src/sections/gateway/shell/page-status", async (importOriginal) => ({
  ...(await importOriginal()),
  isGatewayControlHidden: (name) => name === "managedCredits",
}));

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: state.isCloud, isLoading: false }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { organization_role: "Owner" } }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/sections/settings/UsageSummaryV2/UsageSummaryV2", () => ({
  default: () => <div>Platform usage</div>,
}));
vi.mock("../BudgetManager", () => ({ default: () => <div>Budgets</div> }));
vi.mock("../PaymentMethods", () => ({
  default: () => <div>Payment methods</div>,
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(async (url) => {
        state.calls.push(url);
        return { data: { result: {} } };
      }),
    },
  };
});

const ledgerCalls = () =>
  state.calls.filter((url) => String(url).includes("managed-credits"));

function Where({ into }) {
  into.pathname = useLocation().pathname;
  return null;
}
Where.propTypes = { into: PropTypes.object.isRequired };

function Providers({ children }) {
  return (
    <HelmetProvider>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ThemeProvider
          theme={createTheme({ palette: palette("light"), typography })}
        >
          <MemoryRouter initialEntries={["/dashboard/settings/billing"]}>
            {children}
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>
  );
}
Providers.propTypes = { children: PropTypes.node };

function renderUi(ui) {
  const where = {};
  render(
    <Providers>
      {ui}
      <Where into={where} />
    </Providers>,
  );
  return where;
}

beforeEach(() => {
  state.isCloud = false;
  state.calls = [];
});

describe("Gateway credits on a backend without the ledger", () => {
  it("is not globally hidden, but an unsupported deployment can hide it", () => {
    expect(HIDDEN_GATEWAY_CONTROLS).not.toHaveProperty("managedCredits");
    expect(isGatewayControlHidden("managedCredits")).toBe(true);
  });

  it("useCentralBilling never asks for the ledger and has no credits", async () => {
    const { result } = renderHook(() => useCentralBilling(), {
      wrapper: Providers,
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current).toMatchObject({
      credits: null,
      creditsAvailable: false,
      visible: false,
    });
    expect(ledgerCalls()).toEqual([]);
  });

  it("Billing off cloud sends the user to their profile", async () => {
    const where = renderUi(<BillingPageV2 />);
    await waitFor(() =>
      expect(where.pathname).toBe("/dashboard/settings/profile-settings"),
    );
    expect(ledgerCalls()).toEqual([]);
  });

  it("Billing on cloud shows the platform plan and no gateway credits", async () => {
    state.isCloud = true;
    renderUi(<BillingPageV2 />);
    expect(await screen.findByText("Payment methods")).toBeInTheDocument();
    expect(screen.queryByText(/AI Gateway credits/i)).toBeNull();
    expect(ledgerCalls()).toEqual([]);
  });

  it("Usage Summary off cloud sends the user to Org Settings", async () => {
    const where = renderUi(<UsageSummary />);
    await waitFor(() =>
      expect(where.pathname).toBe("/dashboard/settings/org-settings"),
    );
    expect(ledgerCalls()).toEqual([]);
  });

  it("Usage Summary on cloud shows the platform usage and no gateway split", async () => {
    state.isCloud = true;
    renderUi(<UsageSummary />);
    expect(await screen.findByText("Platform usage")).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "AI Gateway usage" }),
    ).toBeNull();
    expect(ledgerCalls()).toEqual([]);
  });

  it("the Providers and Keys credit chip shows nothing, even to an Owner", () => {
    const { container } = render(
      <Providers>
        <CreditBalanceChip />
      </Providers>,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(screen.queryByText(/Credits/)).toBeNull();
    expect(ledgerCalls()).toEqual([]);
  });
});
