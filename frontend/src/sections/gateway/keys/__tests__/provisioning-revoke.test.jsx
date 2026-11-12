import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, userEvent, waitFor, within } from "src/utils/test-utils";
import { renderPage } from "../../guardrails/__tests__/helpers";
import { endpoints } from "src/utils/axios";
import KeyManagementSection from "../KeyManagementSection";

const state = vi.hoisted(() => ({
  level: 8,
  userId: "actor",
  organizationId: "org-a",
  ready: true,
  keys: [],
  post: vi.fn(),
  snack: vi.fn(),
}));

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: state.level,
    currentOrganizationId: state.organizationId,
    isReady: state.ready,
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
// Keep the real mutation and the real endpoint builder. Only transport is fake.
vi.mock("src/utils/axios", async (importOriginal) => {
  const original = await importOriginal();
  return { ...original, default: { post: state.post } };
});
vi.mock("../hooks/useApiKeys", async (importOriginal) => {
  const original = await importOriginal();
  return {
    ...original,
    useApiKeys: () => ({ data: state.keys, isLoading: false, isError: false }),
    useApiKeyDetail: (id) => ({
      data: state.keys.find((key) => key.id === id), isLoading: false,
    }),
    useSyncApiKeys: () => ({ mutate: vi.fn(), isPending: false }),
    useRotateApiKey: () => ({ mutate: vi.fn(), isPending: false, reset: vi.fn() }),
  };
});
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: undefined, isError: false }),
}));
vi.mock("../../analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: () => ({ data: undefined, isError: false }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: state.snack }));

const warning =
  "Revoked. Any gateway replica that has not received the change yet stops accepting this key at its next key sync.";
const confirmation = "This revokes the key locally and requests revocation at the gateway. If the gateway is unreachable, requests may still be accepted until revocation is synchronized. Local revocation cannot be undone.";

beforeEach(() => {
  vi.clearAllMocks();
  state.level = 8;
  state.userId = "actor";
  state.organizationId = "org-a";
  state.ready = true;
  state.keys = [{
    id: "fixture-row", name: "Lifecycle fixture", status: "active",
    user: "actor", organization: "org-a", allowedModels: [], allowedProviders: [],
  }];
  state.post.mockResolvedValue({ data: { result: {
    id: "fixture-row", status: "revoked", gateway_revocation_confirmed: false, warning,
  } } });
});

const roleFor = (entry) => entry === "row" ? "menuitem" : "button";
async function openActions(user, entry) {
  await user.click(screen.getByRole("button", {
    name: entry === "row"
      ? "Actions for Lifecycle fixture" : "View details for Lifecycle fixture",
  }));
}

// Delete has no existing label/confirmation/action in the pinned key UI.
// Its separate site gate is recorded in the packet, not masked by a fake test.
describe.each(["row", "drawer"])("%s provisioning-state revoke", (entry) => {
  it.each(["failed", "pending", "active"])(
    "%s keeps the existing revoke confirmation and endpoint",
    async (status) => {
      state.keys[0].status = status;
      const user = userEvent.setup();
      renderPage(<KeyManagementSection />);
      await openActions(user, entry);
      const role = roleFor(entry);
      expect(screen.getByRole(role, { name: "Revoke" })).not.toHaveAttribute("aria-disabled", "true");
      if (status === "active") {
        expect(screen.getByRole(role, { name: "Edit" })).toBeVisible();
        expect(screen.getByRole(role, { name: "Rotate" })).toBeVisible();
      } else {
        expect(screen.queryByRole(role, { name: "Edit" })).toBeNull();
        expect(screen.queryByRole(role, { name: "Rotate" })).toBeNull();
      }
      await user.click(screen.getByRole(role, { name: "Revoke" }));
      const dialog = await screen.findByRole("dialog", { name: "Revoke Lifecycle fixture?" });
      expect(within(dialog).getByText(confirmation)).toBeVisible();
      await user.click(within(dialog).getByRole("button", { name: "Revoke key" }));
      await waitFor(() => expect(state.post).toHaveBeenCalledExactlyOnceWith(
        endpoints.gateway.revokeApiKey("fixture-row"),
      ));
      expect(endpoints.gateway.revokeApiKey("fixture-row")).toMatch(
        /\/agentcc\/api-keys\/fixture-row\/revoke\/$/,
      );
      await waitFor(() => expect(state.snack).toHaveBeenCalledWith(warning, { variant: "warning" }));
      // A stale pending detail read cannot restore buttons after local revocation.
      await waitFor(() => expect(screen.getByText(warning)).toBeVisible());
      expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
    },
  );

  it("revoked rows expose neither revoke nor delete", async () => {
    state.keys[0].status = "revoked";
    const user = userEvent.setup();
    renderPage(<KeyManagementSection />);
    if (entry === "row") {
      expect(screen.queryByRole("button", { name: "Actions for Lifecycle fixture" })).toBeNull();
    } else {
      await openActions(user, entry);
    }
    for (const name of ["Revoke", "Delete", "Rotate", "Edit"]) {
      expect(screen.queryByRole(roleFor(entry), { name })).toBeNull();
    }
    expect(state.post).not.toHaveBeenCalled();
  });

  it.each(["failed", "pending"])("a member may revoke its own %s row", async (status) => {
    state.level = 3;
    state.keys[0].status = status;
    const user = userEvent.setup();
    renderPage(<KeyManagementSection />);
    await openActions(user, entry);
    await user.click(screen.getByRole(roleFor(entry), { name: "Revoke" }));
    const dialog = await screen.findByRole("dialog", { name: "Revoke Lifecycle fixture?" });
    await user.click(within(dialog).getByRole("button", { name: "Revoke key" }));
    await waitFor(() => expect(state.post).toHaveBeenCalledExactlyOnceWith(
      endpoints.gateway.revokeApiKey("fixture-row"),
    ));
  });

  it.each(["failed", "pending"])("a member cannot revoke a peer's %s row", async (status) => {
    state.level = 3;
    state.keys[0].status = status;
    state.keys[0].user = "peer";
    const user = userEvent.setup();
    renderPage(<KeyManagementSection />);
    if (entry === "row") {
      expect(screen.getByRole("button", { name: "Actions for Lifecycle fixture" })).toBeDisabled();
    } else {
      await openActions(user, entry);
      expect(screen.getByRole("button", { name: "Revoke" })).toBeDisabled();
    }
    expect(state.post).not.toHaveBeenCalled();
  });

  it.each(["failed", "pending"])("rechecks %s at confirmation after a revoke", async (status) => {
    state.keys[0].status = status;
    const user = userEvent.setup();
    const view = renderPage(<KeyManagementSection />);
    await openActions(user, entry);
    await user.click(screen.getByRole(roleFor(entry), { name: "Revoke" }));
    const dialog = await screen.findByRole("dialog", { name: "Revoke Lifecycle fixture?" });
    state.keys = [{ ...state.keys[0], status: "revoked" }];
    view.rerender(<KeyManagementSection />);
    await user.click(within(dialog).getByRole("button", { name: "Revoke key" }));
    expect(state.post).not.toHaveBeenCalled();
  });
});
