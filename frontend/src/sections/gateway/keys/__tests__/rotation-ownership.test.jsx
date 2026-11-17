import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, userEvent, within } from "src/utils/test-utils";
import { renderPage as render } from "../../guardrails/__tests__/helpers";
import KeyManagementSection from "../KeyManagementSection";
import { canRotate } from "../rotate";

const state = vi.hoisted(() => ({
  level: 8,
  userId: "actor",
  organizationId: "org-a",
  keys: [],
  rotate: vi.fn(),
  revoke: vi.fn(),
}));

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: state.level,
    currentOrganizationId: state.organizationId,
    isReady: true,
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: state.userId } }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock(
  "ag-grid-react",
  async () => await import("../../providers/__tests__/grid-mock"),
);
vi.mock("../../providers/ui/CreditBalanceChip", () => ({ default: () => null }));
vi.mock("react-apexcharts", () => ({ default: () => null }));
vi.mock("../CreateKeyDialog", () => ({ default: () => null }));
vi.mock("../EditKeyDialog", () => ({ default: () => null }));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "fixture-gateway" }),
}));
vi.mock("../hooks/useApiKeys", () => ({
  useApiKeys: () => ({ data: state.keys, isLoading: false, isError: false }),
  useApiKeyDetail: (id) => ({
    data: state.keys.find((key) => key.id === id),
    isLoading: false,
  }),
  useSyncApiKeys: () => ({ mutate: vi.fn(), isPending: false }),
  useRevokeApiKey: () => ({ mutate: state.revoke, isPending: false }),
  useRotateApiKey: () => ({ mutate: state.rotate, isPending: false }),
}));
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: undefined, isError: false }),
}));
vi.mock("../../analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: () => ({ data: undefined, isError: false }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  state.level = 8;
  state.userId = "actor";
  state.organizationId = "org-a";
  state.keys = [{
    id: "old-key",
    name: "Rotation fixture",
    status: "active",
    user: "actor",
    organization: "org-a",
    owner: "An unrelated contact label",
    allowedModels: [],
    allowedProviders: [],
  }];
});

async function openActions(user, entry) {
  await user.click(screen.getByRole("button", {
    name: entry === "row"
      ? "Actions for Rotation fixture"
      : "View details for Rotation fixture",
  }));
}

const actionRole = (entry) => entry === "row" ? "menuitem" : "button";

describe("canonical rotation ownership", () => {
  it("uses the user ID, never a matching owner label", () => {
    expect(canRotate({ user: "actor", status: "active" }, "actor")).toBe(true);
    expect(canRotate({ user: "peer", owner: "actor", status: "active" }, "actor")).toBe(false);
    expect(canRotate({ user: null, owner: "actor", status: "active" }, "actor")).toBe(false);
    expect(canRotate({ user: "actor", status: "revoked" }, "actor")).toBe(false);
    expect(canRotate({ user: "actor", status: "active" }, null)).toBe(false);
    expect(canRotate(undefined, undefined)).toBe(false);
  });

  describe.each([8, 15])("admin level %s", (level) => {
    it.each(["row", "drawer"])("hides another person's Rotate in the %s, retaining Edit and Revoke", async (entry) => {
      state.level = level;
      state.keys[0].user = "peer";
      state.keys[0].owner = state.userId;
      const user = userEvent.setup();
      render(<KeyManagementSection />);
      await openActions(user, entry);
      expect(screen.queryByRole(actionRole(entry), { name: "Rotate" })).toBeNull();
      expect(screen.getByRole(actionRole(entry), { name: "Edit" })).not.toHaveAttribute("aria-disabled", "true");
      const revoke = screen.getByRole(actionRole(entry), { name: "Revoke" });
      expect(revoke).not.toHaveAttribute("aria-disabled", "true");
      await user.click(revoke);
      const dialog = await screen.findByRole("dialog", { name: "Revoke Rotation fixture?" });
      await user.click(within(dialog).getByRole("button", { name: "Revoke key" }));
      expect(state.revoke).toHaveBeenCalledWith("old-key", expect.any(Object));
      expect(state.rotate).not.toHaveBeenCalled();
    });

    it.each(["row", "drawer"])("offers own Rotate in the %s and submits the current key", async (entry) => {
      state.level = level;
      const user = userEvent.setup();
      render(<KeyManagementSection />);
      await openActions(user, entry);
      await user.click(screen.getByRole(actionRole(entry), { name: "Rotate" }));
      const dialog = await screen.findByRole("dialog", { name: "Rotate Rotation fixture?" });
      expect(within(dialog).queryByText(/same.*owner/i)).toBeNull();
      await user.click(within(dialog).getByRole("button", { name: "Rotate key" }));
      expect(state.rotate).toHaveBeenCalledWith(state.keys[0], expect.any(Object));
    });
  });

  it.each(["actor", "organization", "ownership", "role", "status"])("refuses a stale confirmation after %s changes", async (change) => {
    const user = userEvent.setup();
    const view = render(<KeyManagementSection />);
    await openActions(user, "row");
    await user.click(screen.getByRole("menuitem", { name: "Rotate" }));
    const dialog = await screen.findByRole("dialog", { name: "Rotate Rotation fixture?" });
    if (change === "actor") state.userId = "different-actor";
    if (change === "organization") state.organizationId = "different-org";
    if (change === "ownership") state.keys = [{ ...state.keys[0], user: "peer" }];
    if (change === "role") state.level = 3;
    if (change === "status") state.keys = [{ ...state.keys[0], status: "revoked" }];
    view.rerender(<KeyManagementSection />);
    await user.click(within(dialog).getByRole("button", { name: "Rotate key" }));
    expect(state.rotate).not.toHaveBeenCalled();
  });
});
