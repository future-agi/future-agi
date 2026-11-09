import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HIDDEN_GATEWAY_CONTROLS } from "src/sections/gateway/shell/page-status";
import CreditBalanceChip from "src/sections/gateway/providers/ui/CreditBalanceChip";
import BillingPage from "../BillingPage";
import { useCentralBilling } from "../useCentralBilling";

const state = vi.hoisted(() => ({ isCloud: false, calls: [] }));
vi.mock("src/hooks/useDeploymentMode", () => ({ useDeploymentMode: () => ({ isCloud: state.isCloud, isLoading: false }) }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ user: { organization_role: "Owner" } }) }));
vi.mock("src/contexts/WorkspaceContext", () => ({ useWorkspace: () => ({ currentWorkspaceRole: null }) }));
vi.mock("../BudgetManager", () => ({ default: () => <div>Platform budgets</div> }));
vi.mock("../PaymentMethods", () => ({ default: () => <div>Platform payment methods</div> }));
vi.mock("src/utils/axios", async (original) => {
  const actual = await original();
  return { ...actual, default: { ...actual.default, get: vi.fn(async (url) => {
    state.calls.push(url);
    return { data: { result: String(url).includes("managed-credits") ? {
      enabled: false, billing_available: false, gateway: null,
      granted_usd: "0", settled_spend_usd: "0", available_usd: "0",
      pending_settlement_usd: "0", last_reconciled_at: null,
    } : {} } };
  }) } };
});

function Providers({ children }) {
  const [client] = React.useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
  return <HelmetProvider><QueryClientProvider client={client}><ThemeProvider theme={createTheme()}>
    <MemoryRouter initialEntries={["/dashboard/settings/billing"]}>{children}</MemoryRouter>
  </ThemeProvider></QueryClientProvider></HelmetProvider>;
}
Providers.propTypes = { children: PropTypes.node };
function Location() { return <output data-testid="location">{useLocation().pathname}</output>; }
const noCreditUi = () => {
  expect(screen.queryByText(/Future AGI credits|AI Gateway credits/i)).toBeNull();
  expect(screen.queryByRole("button", { name: /Add credits|Top.up/i })).toBeNull();
  expect(screen.queryByRole("link", { name: /credits/i })).toBeNull();
};
beforeEach(() => { state.isCloud = false; state.calls = []; });

describe("credit visibility with no managed provider configured", () => {
  it("uses server availability, not a frontend hide", async () => {
    expect(HIDDEN_GATEWAY_CONTROLS).not.toHaveProperty("managedCredits");
    const { result } = renderHook(() => useCentralBilling(), { wrapper: Providers });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(state.calls.some((url) => String(url).includes("managed-credits"))).toBe(true);
    expect(result.current).toMatchObject({ credits: null, creditsAvailable: false, visible: false });
  });
  it("redirects the unsupported billing route and renders no credits chip", async () => {
    render(<Providers><BillingPage /><CreditBalanceChip /><Location /></Providers>);
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/dashboard/settings/profile-settings"));
    noCreditUi();
  });
  it("retains cloud platform billing but renders no Future AGI credit controls", async () => {
    state.isCloud = true;
    render(<Providers><BillingPage /><CreditBalanceChip /><Location /></Providers>);
    expect(await screen.findByText("Platform payment methods")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/dashboard/settings/billing");
    noCreditUi();
  });
});
