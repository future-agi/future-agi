import React from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useNavSettingsData } from "src/layouts/dashboard/config-navigation";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import BillingPage from "../BillingPage";
import { creditKeys } from "../gateway-credits/api";

const BILLING = "/dashboard/settings/billing";
const PROFILE = "/dashboard/settings/profile-settings";
const UNAVAILABLE = {
  status: false,
  statusCode: 503,
  code: "billing_unavailable",
  result: "The managed credit ledger is not installed in this build.",
};
const OPERATION_FAILED = {
  ...UNAVAILABLE,
  result: "The managed credit operation could not be completed.",
};
const SUMMARY = {
  enabled: true,
  billing_available: false,
  available_usd: "25",
  granted_usd: "25",
  settled_spend_usd: "0",
  pending_settlement_usd: "0",
};
const state = vi.hoisted(() => ({
  isCloud: false,
  role: "Owner",
  summary: null,
  failure: null,
  failuresLeft: Infinity,
  calls: [],
  platformFails: false,
}));

vi.mock("src/sections/gateway/shell/page-status", async (original) => ({
  ...(await original()),
  isGatewayControlHidden: () => false,
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isCloud: state.isCloud,
    isOSS: !state.isCloud,
    isEE: false,
    isLoading: false,
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: {
      id: "summary-recovery-owner",
      organization_role: state.role,
      role: state.role,
      ws_enabled: false,
    },
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
  useWorkspaceFromList: () => ({ workspace: null }),
}));
vi.mock("src/utils/axios", async (original) => {
  const actual = await original();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(async (url) => {
        state.calls.push(url);
        if (url === actual.endpoints.gateway.managedCredits) {
          if (state.failure && state.failuresLeft > 0) {
            state.failuresLeft -= 1;
            throw state.failure;
          }
          return { data: { result: state.summary } };
        }
        if (
          state.platformFails &&
          url === actual.endpoints.settings.v2.billingOverview
        ) {
          throw new Error("platform unavailable");
        }
        return { data: { result: {} } };
      }),
    },
  };
});

// Keep the page, navigation, summary query and wallet's own error gate real.
// Only leaf panels are stubbed; no payment or ledger mutation is exercised.
vi.mock("../BudgetManager", () => ({ default: () => <div>Budgets</div> }));
vi.mock("../PaymentMethods", () => ({
  default: () => <div>Payment methods</div>,
}));
vi.mock("../gateway-credits/AutoReloadForm", () => ({ default: () => null }));
vi.mock("../gateway-credits/CreditLedger", () => ({ default: () => null }));
vi.mock("../gateway-credits/CreditStatement", () => ({ default: () => null }));
vi.mock("../gateway-credits/TopUpDialog", () => ({ default: () => null }));

function SettingsNavigation() {
  const sections = useNavSettingsData();
  const billing = sections
    .flatMap((section) => section.items)
    .find((item) => item.path === BILLING);
  return billing ? <a href={billing.path}>Billing navigation</a> : null;
}

function Location() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

const clients = [];
function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <MemoryRouter initialEntries={[BILLING]}>
          <SettingsNavigation />
          <Routes>
            <Route path={BILLING} element={<BillingPage />} />
            <Route path={PROFILE} element={<div>Profile destination</div>} />
          </Routes>
          <Location />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return client;
}

const summaryReads = () =>
  state.calls.filter((url) => url === "/agentcc/managed-credits/");
const expectBilling = () => {
  expect(screen.getByTestId("location")).toHaveTextContent(BILLING);
  expect(screen.getByRole("heading", { name: "Billing" })).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Billing navigation" }),
  ).toHaveAttribute("href", BILLING);
};
async function expectCreditError() {
  expect(
    await screen.findByText("AI Gateway credits couldn't be loaded"),
  ).toBeInTheDocument();
  const region = screen.getByRole("region", { name: "AI Gateway credits" });
  expect(region).toHaveAttribute("id", "ai-gateway-credits");
  expect(within(region).getByRole("alert")).toBeInTheDocument();
  expect(within(region).getByRole("button", { name: "Retry" })).toBeEnabled();
  expectBilling();
  return region;
}

beforeEach(() => {
  state.isCloud = false;
  state.role = "Owner";
  state.summary = { ...SUMMARY };
  state.failure = null;
  state.failuresLeft = Infinity;
  state.calls = [];
  state.platformFails = false;
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("J12-002 credit-summary failure recovery", () => {
  it.each(["Owner", "Admin"])(
    "J12-002 initial 503 once keeps %s on Billing with Retry and wallet navigation, then recovers",
    async (role) => {
      state.role = role;
      state.failure = OPERATION_FAILED;
      state.failuresLeft = 1;
      renderPage();
      const region = await expectCreditError();
      expect(summaryReads()).toHaveLength(1);
      expect(screen.queryByText("Available")).toBeNull();
      expect(state.calls.filter((url) => url.startsWith("/usage/v2/"))).toEqual(
        [],
      );
      await userEvent.click(
        within(region).getByRole("button", { name: "Retry" }),
      );
      expect(await screen.findByText("Available")).toBeInTheDocument();
      expect(screen.getByText("Available").parentElement).toHaveTextContent(
        "$25.00",
      );
      expect(summaryReads()).toHaveLength(2);
      expect(
        screen.queryByText("AI Gateway credits couldn't be loaded"),
      ).toBeNull();
      expectBilling();
    },
  );

  it.each([
    ["network failure", new TypeError("Network Error")],
    ["untyped 503", { statusCode: 503, message: "Service unavailable" }],
    ["untyped 500", { statusCode: 500, message: "Internal server error" }],
    ["untyped 404", { statusCode: 404, message: "Not found" }],
    ["code-only 503", { statusCode: 503, code: "billing_unavailable" }],
    [
      "503 Error token",
      Object.assign(new Error("billing_unavailable"), { statusCode: 503 }),
    ],
  ])("J12-002 %s is not proof of a missing ledger", async (_label, failure) => {
    state.failure = failure;
    renderPage();
    await expectCreditError();
    expect(summaryReads()).toHaveLength(1);
  });

  it.each([false, true])(
    "J12-002 cached-summary 503 leaves a visible credit error and recovers (cloud: %s)",
    async (isCloud) => {
      state.isCloud = isCloud;
      const client = renderPage();
      expect(await screen.findByText("Available")).toBeInTheDocument();
      state.failure = OPERATION_FAILED;
      await act(async () => {
        await client.invalidateQueries({ queryKey: creditKeys.summary });
      });
      const region = await expectCreditError();
      expect(client.getQueryData(creditKeys.summary).data.result.enabled).toBe(
        true,
      );
      expect(screen.queryByText("Available")).toBeNull();
      if (isCloud) {
        expect(screen.getByText("Invoice history")).toBeInTheDocument();
        expect(screen.getByText("Payment methods")).toBeInTheDocument();
      }
      state.failure = null;
      await userEvent.click(
        within(region).getByRole("button", { name: "Retry" }),
      );
      expect(await screen.findByText("Available")).toBeInTheDocument();
      expect(summaryReads()).toHaveLength(3);
      expectBilling();
    },
  );

  it("J12-002 cloud platform and credit failures each keep their own Retry", async () => {
    state.isCloud = true;
    state.platformFails = true;
    state.failure = OPERATION_FAILED;
    renderPage();
    const region = await expectCreditError();
    expect(
      await screen.findByText("Billing couldn't be loaded"),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(2);
    state.failure = null;
    await userEvent.click(
      within(region).getByRole("button", { name: "Retry" }),
    );
    expect(await screen.findByText("Available")).toBeInTheDocument();
    expect(screen.getByText("Billing couldn't be loaded")).toBeInTheDocument();
    expectBilling();
  });

  it.each([
    ["current missing-ledger 503", UNAVAILABLE],
    ["legacy typed 501", { statusCode: 501, code: "billing_unavailable" }],
    ["legacy no-ledger Error", new Error("billing_unavailable")],
  ])(
    "J12-002 control: %s still redirects off cloud",
    async (_label, failure) => {
      state.failure = failure;
      renderPage();
      await waitFor(() =>
        expect(screen.getByTestId("location")).toHaveTextContent(PROFILE),
      );
      expect(
        screen.queryByRole("link", { name: "Billing navigation" }),
      ).toBeNull();
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    },
  );

  it("J12-002 control: a successful disabled summary still redirects off cloud", async () => {
    state.summary.enabled = false;
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(PROFILE),
    );
    expect(
      screen.queryByRole("link", { name: "Billing navigation" }),
    ).toBeNull();
  });

  it("J12-002 control: enabled credits stay on Billing", async () => {
    renderPage();
    expect(await screen.findByText("Available")).toBeInTheDocument();
    expectBilling();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("J12-002 control: cloud keeps platform billing without a ledger", async () => {
    state.isCloud = true;
    state.failure = UNAVAILABLE;
    renderPage();
    expect(await screen.findByText("Payment methods")).toBeInTheDocument();
    expect(screen.getByText("Invoice history")).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "AI Gateway credits" }),
    ).toBeNull();
    expectBilling();
  });
});
