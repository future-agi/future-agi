import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter } from "react-router-dom";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { paths } from "src/routes/paths";
import {
  useNavGatewayData,
  useNavSettingsData,
} from "src/layouts/dashboard/config-navigation";
import { dashboardRoutes } from "src/routes/sections/dashboard";
import { hasEnabledCredits, useCentralBilling } from "../useCentralBilling";

// One central billing: Settings > Billing and Usage Summary show whenever the
// org has something to bill there, on cloud (the platform plan) or wherever
// the gateway's managed-credit ledger answers (self-hosted EE included).

// These cases cover central billing once the gateway's credit ledger is
// served; on this backend it is hidden (page-status.js
// HIDDEN_GATEWAY_CONTROLS.managedCredits, see credits-hidden.test.jsx).
vi.mock("src/sections/gateway/shell/page-status", async (importOriginal) => ({
  ...(await importOriginal()),
  isGatewayControlHidden: () => false,
}));

const state = vi.hoisted(() => ({
  isCloud: false,
  creditsStatus: 200,
  role: "Owner",
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
      id: "u1",
      role: state.role,
      organization_role: state.role,
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
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(async (url) => {
        if (url === actual.endpoints.gateway.managedCredits) {
          if (state.creditsStatus === 200) {
            return {
              data: {
                status: true,
                result: { enabled: true, available_usd: 34.99 },
              },
            };
          }
          const error = new Error("billing_unavailable");
          error.statusCode = state.creditsStatus;
          throw error;
        }
        throw new Error(`unmocked GET ${url}`);
      }),
    },
  };
});

function wrapperAt(pathname) {
  function Wrapper({ children }) {
    return (
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <MemoryRouter initialEntries={[pathname]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
}

const settingsTitles = (sections) =>
  sections.flatMap((section) => section.items.map((item) => item.title));

describe("central billing in the settings nav", () => {
  beforeEach(() => {
    state.isCloud = false;
    state.creditsStatus = 200;
    state.role = "Owner";
  });

  it("lists Billing and Usage Summary off cloud when gateway credits answer", async () => {
    const { result } = renderHook(() => useNavSettingsData(), {
      wrapper: wrapperAt("/dashboard/settings/profile-settings"),
    });
    await waitFor(() =>
      expect(settingsTitles(result.current)).toContain("Billing"),
    );
    expect(settingsTitles(result.current)).toContain("Usage summary");
    expect(settingsTitles(result.current)).not.toContain("Plans & pricing");
  });

  it("leaves them out when this build has no credit ledger", async () => {
    state.creditsStatus = 501;
    const { result } = renderHook(() => useNavSettingsData(), {
      wrapper: wrapperAt("/dashboard/settings/profile-settings"),
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(settingsTitles(result.current)).not.toContain("Billing");
    expect(settingsTitles(result.current)).not.toContain("Usage summary");
  });

  it("keeps them on cloud whatever the gateway says", async () => {
    state.isCloud = true;
    state.creditsStatus = 501;
    const { result } = renderHook(() => useNavSettingsData(), {
      wrapper: wrapperAt("/dashboard/settings/profile-settings"),
    });
    await waitFor(() =>
      expect(settingsTitles(result.current)).toContain("Billing"),
    );
    expect(settingsTitles(result.current)).toContain("Plans & pricing");
  });
});

// Billing & credits lives
// in Settings (central billing). The gateway rail keeps its 6 groups (the
// MCP page is hidden, so its group drops) and no Billing item; Gateway settings links Billing & credits for a billing role.
describe("central billing and the gateway rail", () => {
  beforeEach(() => {
    state.isCloud = false;
    state.creditsStatus = 200;
    state.role = "Owner";
  });

  const billingItem = (groups) =>
    groups
      .flatMap((group) => group.items)
      .find(
        (item) =>
          item.path === paths.dashboard.settings.billing ||
          item.title === "Billing & credits",
      );

  it("has no Billing & credits item: it lives in Settings, and the rail keeps 6 groups", async () => {
    const { result } = renderHook(() => useNavGatewayData(), {
      wrapper: wrapperAt("/dashboard/gateway/logs"),
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(billingItem(result.current)).toBeUndefined();
    expect(result.current.map((group) => group.id)).toEqual([
      "overview",
      "models",
      "providers",
      "guardrails",
      "observability",
      "settings",
    ]);
    expect(
      result.current
        .find((group) => group.id === "settings")
        .items.map((item) => item.title),
    ).toEqual(["Gateway settings", "Budgets"]);
  });

  it("reads no credit ledger to draw the rail", async () => {
    const { default: axiosInstance, endpoints } = await import(
      "src/utils/axios"
    );
    axiosInstance.get.mockClear();
    renderHook(() => useNavGatewayData(), {
      wrapper: wrapperAt("/dashboard/gateway/logs"),
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(axiosInstance.get).not.toHaveBeenCalledWith(
      endpoints.gateway.managedCredits,
    );
  });
});

describe("the Billing route", () => {
  const settingsChildren = (options) => {
    const [dashboardRoute] = dashboardRoutes(
      { organization_role: "Owner", ws_enabled: false },
      null,
      options,
    );
    return dashboardRoute.children
      .find((child) => child.path === "settings")
      .children.map((child) => child.path);
  };

  it("is registered off cloud too; Plans & Pricing stays cloud only", () => {
    expect(settingsChildren({ isCloud: false })).toContain("billing");
    expect(settingsChildren({ isCloud: false })).not.toContain("pricing");
    expect(settingsChildren({ isCloud: true })).toEqual(
      expect.arrayContaining(["billing", "pricing"]),
    );
  });
});

describe("fresh organization credit visibility", () => {
  it("requires organization enablement regardless of balances or history", () => {
    const fresh = {
      enabled: false,
      managed_models_available: false,
      available_usd: "0",
      granted_usd: "0",
      settled_spend_usd: "0",
      pending_settlement_usd: "0",
    };
    expect(hasEnabledCredits(fresh)).toBe(false);
    expect(hasEnabledCredits(null)).toBe(false);
    expect(hasEnabledCredits({ ...fresh, enabled: true })).toBe(true);
    for (const field of [
      "granted_usd",
      "settled_spend_usd",
      "available_usd",
      "pending_settlement_usd",
    ]) {
      expect(hasEnabledCredits({ ...fresh, [field]: "0.000001" })).toBe(false);
    }
    expect(
      hasEnabledCredits({
        ...fresh,
        last_reconciled_at: "2026-10-28T00:00:00Z",
      }),
    ).toBe(false);
    expect(
      hasEnabledCredits({
        ...fresh,
        billing_available: true,
        managed_models_available: true,
      }),
    ).toBe(false);
  });

  it("does not request organization credits for a cloud workspace admin", async () => {
    state.role = "Member";
    state.isCloud = true;
    const { default: axiosInstance } = await import("src/utils/axios");
    axiosInstance.get.mockClear();
    const { result } = renderHook(() => useCentralBilling(), {
      wrapper: wrapperAt("/dashboard/settings/billing"),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.visible).toBe(true);
    expect(result.current.creditsAvailable).toBe(false);
    expect(result.current.credits).toBeNull();
    expect(
      axiosInstance.get.mock.calls.filter(([url]) =>
        url.includes("/managed-credits/"),
      ),
    ).toEqual([]);
  });
});
