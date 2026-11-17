import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { render, screen } from "src/utils/test-utils";

import { useCentralBilling } from "../useCentralBilling";
import CreditBalanceChip from "src/sections/gateway/providers/ui/CreditBalanceChip";
import BillingCreditsPanel from "src/sections/gateway/settings/BillingCreditsPanel";

const state = vi.hoisted(() => ({ summary: null, isCloud: true }));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isCloud: state.isCloud, isLoading: false }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { organization_role: "Owner" } }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("../gateway-credits/api", () => ({
  useGatewayCreditSummary: () => ({ data: state.summary, isLoading: false }),
}));

beforeEach(() => {
  state.isCloud = true;
  state.summary = {
    enabled: false,
    billing_available: true,
    managed_models_available: true,
    available_usd: "25",
    granted_usd: "50",
    settled_spend_usd: "25",
    last_reconciled_at: "2026-10-28T00:00:00Z",
  };
});

describe("F4 new wallet organization gate", () => {
  it.each([false, undefined])(
    "keeps cloud Billing visible without wallet access when enabled is %s despite history",
    (enabled) => {
      state.summary.enabled = enabled;
      const { result } = renderHook(() => useCentralBilling());
      expect(result.current.visible).toBe(true);
      expect(result.current.creditsAvailable).toBe(false);
      expect(result.current.credits).toBeNull();
    },
  );

  it("keeps the new credit chip and wallet panel dark despite history and purchasing availability", () => {
    render(
      <>
        <CreditBalanceChip />
        <BillingCreditsPanel />
      </>,
    );
    expect(
      screen.queryAllByText(/Future AGI credits|Billing & credits/i),
    ).toHaveLength(0);
    expect(screen.queryByRole("link", { name: /credits/i })).toBeNull();
  });

  it.each([
    ["hosted", true, false],
    ["hosted", true, true],
    ["self-host", false, false],
    ["self-host", false, true],
  ])(
    "817(4) states the %s billing destination truthfully (purchase available: %s/%s)",
    (_mode, isCloud, canBuy) => {
      state.isCloud = isCloud;
      state.summary.enabled = true;
      state.summary.billing_available = canBuy;
      render(<BillingCreditsPanel />);
      const what = canBuy
        ? "balance, ledger, monthly statement, top-ups and low-balance alert"
        : "balance, ledger, monthly statement and low-balance alert";
      const copy = isCloud
        ? `Calls to Future AGI models are paid from your organization's Future AGI credits. Its ${what} are in Settings > Billing, with the rest of your plan.`
        : `Settings > Billing shows your organization's Future AGI credit ${what}.`;
      expect(screen.getByText(copy)).toBeInTheDocument();
      expect(
        screen.getByRole("link", { name: "Open Billing & credits" }),
      ).toHaveAttribute(
        "href",
        "/dashboard/settings/billing#ai-gateway-credits",
      );
      if (!isCloud)
        expect(screen.queryByText(/with the rest of your plan/)).toBeNull();
    },
  );

  it.each([true, false])(
    "keeps the panel dark when disabled (hosted: %s)",
    (isCloud) => {
      state.isCloud = isCloud;
      render(<BillingCreditsPanel />);
      expect(screen.queryByText("Billing & credits")).toBeNull();
    },
  );

  it("allows the wallet for an enabled organization", () => {
    state.summary.enabled = true;
    const { result } = renderHook(() => useCentralBilling());
    expect(result.current.creditsAvailable).toBe(true);
    expect(result.current.credits).toBe(state.summary);
  });
});
