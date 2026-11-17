import React from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import BillingPage from "../BillingPage";
import { creditKeys } from "../gateway-credits/api";

const BILLING = "/dashboard/settings/billing";
const SESSION_ID = "cs_summary_recovery";
const RETURN_URL = `${BILLING}?topup=success&session_id=${SESSION_ID}&keep=1#ai-gateway-credits`;
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
  summary: null,
  failuresLeft: 0,
  calls: [],
  announce: vi.fn(),
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
    user: { organization_role: "Owner", role: "Owner" },
  }),
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => state.announce(...args),
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
          if (state.failuresLeft > 0) {
            state.failuresLeft -= 1;
            throw {
              status: false,
              statusCode: 503,
              code: "billing_unavailable",
              result: "The managed credit operation could not be completed.",
            };
          }
          return { data: { result: state.summary } };
        }
        if (
          url ===
          actual.endpoints.gateway.managedCreditCheckoutStatus(SESSION_ID)
        ) {
          return {
            data: {
              result: { credited: false, status: "pending", session_id: null },
            },
          };
        }
        if (url === actual.endpoints.settings.v2.billingOverview) {
          return { data: { result: { plan: "free" } } };
        }
        if (url === actual.endpoints.settings.v2.notifications) {
          return { data: { result: { banners: [] } } };
        }
        if (url === actual.endpoints.settings.v2.invoices) {
          return { data: { result: { invoices: [] } } };
        }
        throw new Error(`Unexpected billing read: ${url}`);
      }),
    },
  };
});

// Keep BillingPage, useCentralBilling, the summary/status API helpers,
// GatewayCreditsSection and its useCheckoutReturn effect real. Only unrelated
// leaf panels are stubbed; the test never calls Retry or refreshes the query.
vi.mock("../BudgetManager", () => ({ default: () => <div>Budgets</div> }));
vi.mock("../PaymentMethods", () => ({
  default: () => <div>Payment methods</div>,
}));
vi.mock("../gateway-credits/AutoReloadForm", () => ({ default: () => null }));
vi.mock("../gateway-credits/CreditLedger", () => ({ default: () => null }));
vi.mock("../gateway-credits/CreditStatement", () => ({ default: () => null }));
vi.mock("../gateway-credits/TopUpDialog", () => ({ default: () => null }));

function Location() {
  const { pathname, search, hash } = useLocation();
  return <output data-testid="location">{pathname + search + hash}</output>;
}

const clients = [];
function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  // The enabled summary was cached before leaving Billing for checkout.
  client.setQueryData(creditKeys.summary, { data: { result: SUMMARY } });
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <MemoryRouter initialEntries={[RETURN_URL]}>
          <Routes>
            <Route path={BILLING} element={<BillingPage />} />
            <Route
              path="/dashboard/settings/profile-settings"
              element={<div>Profile destination</div>}
            />
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
const checkoutReads = () =>
  state.calls.filter((url) => url.includes(`/checkout/${SESSION_ID}/`));
async function advance(ms) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
  // Flush query observer notifications scheduled by the preceding React commit.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  state.isCloud = false;
  state.summary = { ...SUMMARY };
  state.failuresLeft = 1;
  state.calls = [];
  state.announce.mockReset();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.useRealTimers();
});

describe("J12-002 checkout-return summary recovery", () => {
  it.each([false, true])(
    "J12-002 COR-001 H7-C25-003 keeps the pending notice and bounded automatic recovery after a poll-limit 503 (cloud: %s)",
    async (isCloud) => {
      state.isCloud = isCloud;
      const client = renderPage();
      await advance(0);
      expect(screen.getByText("Available").parentElement).toHaveTextContent(
        "$25.00",
      );
      expect(screen.getByTestId("location").textContent).toBe(RETURN_URL);
      expect(checkoutReads()).toHaveLength(1);
      expect(summaryReads()).toHaveLength(0);
      expect(state.announce).not.toHaveBeenCalled();

      // Twenty real checkout polls reach the limit; its summary refresh fails
      // once. Let React process that error before the slow follow-up is due.
      await advance(31_000);
      expect(checkoutReads()).toHaveLength(20);
      expect(summaryReads()).toHaveLength(1);
      expect(client.getQueryState(creditKeys.summary).status).toBe("error");
      expect(client.getQueryData(creditKeys.summary).data.result.enabled).toBe(
        true,
      );
      const region = screen.getByRole("region", {
        name: "AI Gateway credits",
      });
      expect(region).toHaveAttribute("id", "ai-gateway-credits");
      expect(within(region).getByRole("alert")).toHaveTextContent(
        "AI Gateway credits couldn't be loaded",
      );
      expect(
        within(region).getByRole("button", { name: "Retry" }),
      ).toBeEnabled();
      expect(screen.queryByText("Available")).toBeNull();
      expect(screen.queryByText("Profile destination")).toBeNull();
      expect(screen.getByTestId("location").textContent).toBe(RETURN_URL);
      expect(state.announce).toHaveBeenCalledTimes(1);
      expect(state.announce).toHaveBeenCalledWith(
        "Your payment is still being processed. We will refresh the balance once more shortly. If it is still pending, refresh Billing later.",
        { variant: "info" },
      );

      // Settlement and service recovery happen before the single 30s follow-up.
      // Changing this fixture alone neither refetches nor clicks Retry.
      state.summary = { ...SUMMARY, available_usd: "75", granted_usd: "75" };
      await advance(26_000);
      expect(summaryReads()).toHaveLength(1);
      expect(checkoutReads()).toHaveLength(20);
      expect(screen.queryByText("Available")).toBeNull();
      expect(screen.getByTestId("location").textContent).toBe(RETURN_URL);

      await advance(3_000);
      expect(summaryReads()).toHaveLength(2);
      expect(checkoutReads()).toHaveLength(20);
      expect(client.getQueryState(creditKeys.summary).status).toBe("success");
      expect(screen.getByText("Available").parentElement).toHaveTextContent(
        "$75.00",
      );
      expect(
        screen.queryByText("AI Gateway credits couldn't be loaded"),
      ).toBeNull();
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
      expect(
        screen.getByRole("heading", { name: "Billing" }),
      ).toBeInTheDocument();
      expect(screen.getByTestId("location").textContent).toBe(
        `${BILLING}?keep=1#ai-gateway-credits`,
      );
      expect(state.announce).toHaveBeenCalledTimes(1);
      expect(state.announce).not.toHaveBeenCalledWith(
        "Credits added to your AI Gateway balance.",
        expect.anything(),
      );

      // No remounted owner, restarted fast polling or unbounded refresh loop.
      await advance(120_000);
      expect(checkoutReads()).toHaveLength(20);
      expect(summaryReads()).toHaveLength(2);
      expect(state.announce).toHaveBeenCalledTimes(1);
    },
  );
});
