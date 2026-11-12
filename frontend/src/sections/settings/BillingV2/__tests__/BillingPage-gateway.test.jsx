import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter, useLocation } from "react-router-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import BillingPageV2 from "../BillingPage";

// Settings > Billing is the one billing page: the platform plan on cloud and
// the AI Gateway credits wherever the gateway's ledger answers.

// These cases cover central billing once the gateway's credit ledger is
// served; on this backend it is hidden (page-status.js
// HIDDEN_GATEWAY_CONTROLS.managedCredits, see credits-hidden.test.jsx).
vi.mock("src/sections/gateway/shell/page-status", async (importOriginal) => ({
  ...(await importOriginal()),
  isGatewayControlHidden: () => false,
}));

const state = vi.hoisted(() => ({
  isCloud: false,
  credits: true,
  enabled: true,
  role: "Owner",
  platformFails: false,
  calls: [],
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { organization_role: state.role },
    role: "workspace_admin",
  }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: state.isCloud, isLoading: false }),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(async (url) => {
        state.calls.push(url);
        if (state.platformFails && url.startsWith("/usage/v2/"))
          throw new Error("platform unavailable");
        if (url === actual.endpoints.gateway.managedCredits) {
          if (!state.credits) throw new Error("billing_unavailable");
          return {
            data: {
              result: {
                enabled: state.enabled,
                billing_available: true,
                available_usd: 1,
                last_reconciled_at: "2026-10-28T00:00:00Z",
              },
            },
          };
        }
        return { data: { result: {} } };
      }),
    },
  };
});
vi.mock("../gateway-credits/GatewayCreditsSection", () => ({
  default: () => <section aria-label="AI Gateway credits" />,
}));
vi.mock("../BudgetManager", () => ({ default: () => <div>Budgets</div> }));
vi.mock("../PaymentMethods", () => ({
  default: () => <div>Payment methods</div>,
}));

function Where({ into }) {
  into.pathname = useLocation().pathname;
  return null;
}
Where.propTypes = { into: PropTypes.object.isRequired };

function renderPage() {
  const where = {};
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <MemoryRouter initialEntries={["/dashboard/settings/billing"]}>
          <BillingPageV2 />
          <Where into={where} />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return where;
}

describe("Settings > Billing with AI Gateway credits", () => {
  beforeEach(() => {
    state.isCloud = false;
    state.credits = true;
    state.enabled = true;
    state.role = "Owner";
    state.platformFails = false;
    state.calls = [];
  });

  it("F4 keeps cloud Billing, invoices and payment methods visible for a disabled organization with credit history", async () => {
    state.isCloud = true;
    state.enabled = false;
    const where = renderPage();
    expect(await screen.findByText("Invoice history")).toBeInTheDocument();
    expect(screen.getByText("Payment methods")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Billing" }),
    ).toBeInTheDocument();
    expect(where.pathname).toBe("/dashboard/settings/billing");
    expect(
      screen.queryByRole("region", { name: "AI Gateway credits" }),
    ).toBeNull();
  });

  it("off cloud shows only the gateway credits and asks no platform billing API", async () => {
    renderPage();
    expect(
      await screen.findByRole("region", { name: "AI Gateway credits" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Billing" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Invoice history")).not.toBeInTheDocument();
    expect(screen.queryByText("Payment methods")).not.toBeInTheDocument();
    expect(state.calls.filter((url) => url.startsWith("/usage/v2/"))).toEqual(
      [],
    );
  });

  it("off cloud without a credit ledger sends the user to their profile", async () => {
    state.credits = false;
    const where = renderPage();
    await waitFor(() =>
      expect(where.pathname).toBe("/dashboard/settings/profile-settings"),
    );
  });

  it("on cloud shows the platform billing and the gateway credits", async () => {
    state.isCloud = true;
    renderPage();
    expect(
      await screen.findByRole("region", { name: "AI Gateway credits" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Invoice history")).toBeInTheDocument();
    expect(screen.getByText("Payment methods")).toBeInTheDocument();
  });

  it("keeps credit history visible when the cloud platform billing read fails", async () => {
    state.isCloud = true;
    state.platformFails = true;
    renderPage();
    expect(
      await screen.findByRole("region", { name: "AI Gateway credits" }),
    ).toBeInTheDocument();
    expect(state.calls.some((url) => url.startsWith("/usage/v2/"))).toBe(true);
    expect(state.calls).toContain("/agentcc/managed-credits/");
  });

  it("gives a cloud workspace admin platform billing without an organization credit read", async () => {
    state.isCloud = true;
    state.role = "Member";
    renderPage();
    expect(await screen.findByText("Payment methods")).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "AI Gateway credits" }),
    ).toBeNull();
    expect(
      state.calls.filter((url) => url.includes("/managed-credits/")),
    ).toEqual([]);
  });
});
