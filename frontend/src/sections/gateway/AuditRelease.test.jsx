import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, within, waitFor } from "src/utils/test-utils";
import { renderPage as render } from "./guardrails/__tests__/helpers";
import userEvent from "@testing-library/user-event";
import KeyDetailDrawer from "./keys/KeyDetailDrawer";
import KeyManagementSection from "./keys/KeyManagementSection";
import ModelComparison from "./analytics/ModelComparison";
import { lastGrid } from "./providers/__tests__/grid-mock";
const state = vi.hoisted(() => ({
  keys: [],
  canEdit: true,
  models: [],
  revoke: vi.fn(),
}));
vi.mock(
  "ag-grid-react",
  async () => await import("./providers/__tests__/grid-mock"),
);
vi.mock("react-apexcharts", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: state.canEdit ? 15 : 3,
    currentOrganizationId: "audit-org",
    isReady: true,
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "audit-user" } }),
}));
vi.mock("./providers/ui/CreditBalanceChip", () => ({ default: () => null }));
vi.mock("./keys/CreateKeyDialog", () => ({ default: () => null }));
vi.mock("./keys/EditKeyDialog", () => ({ default: () => null }));
vi.mock("./keys/hooks/useApiKeys", () => ({
  useApiKeyDetail: (id) => ({
    data: state.keys.find((k) => k.id === id),
    isLoading: false,
  }),
  useApiKeys: () => ({ data: state.keys, isLoading: false }),
  useSyncApiKeys: () => ({ mutate: vi.fn(), isPending: false }),
  useRotateApiKey: () => ({ mutate: vi.fn(), isPending: false }),
  useRevokeApiKey: () => ({ mutate: state.revoke, isPending: false }),
}));
vi.mock("./hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({
    canEdit: state.canEdit,
    disabledReason: state.canEdit ? null : "Owner or Admin only",
  }),
}));
vi.mock("./context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default" }),
}));
vi.mock("./analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: undefined, isError: false }),
}));
vi.mock("./analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: () => ({ data: undefined, isError: false }),
}));
vi.mock("./analytics/hooks/useAnalyticsModels", () => ({
  useAnalyticsModels: () => ({
    data: { models: state.models },
    isLoading: false,
  }),
}));
const warning =
  "Key revoked locally but the gateway could not be reached. The gateway will be updated on next sync.";
beforeEach(() => {
  state.keys = [
    {
      id: "key-uuid",
      organization: "audit-org",
      name: "Example key",
      status: "active",
      allowedModels: [],
      allowedProviders: [],
    },
  ];
  state.canEdit = true;
  state.revoke.mockReset();
  state.models = [
    {
      model: "model-nine",
      provider: "zeta",
      request_count: 9,
      total_cost: "9.000000",
    },
    {
      model: "model-ten",
      provider: "alpha",
      request_count: 10,
      total_cost: "10.000000",
    },
    {
      model: "model-fraction",
      provider: "beta",
      request_count: 1,
      total_cost: "0.100000",
    },
  ];
});
async function openKey(user, name = "Example key") {
  await user.click(
    await screen.findByRole("button", { name: `View details for ${name}` }),
  );
  return screen.findByRole("dialog", { name });
}
async function revoke(user) {
  const drawer = await openKey(user);
  await user.click(within(drawer).getByRole("button", { name: "Revoke" }));
  const confirmation = await screen.findByRole("dialog", {
    name: "Revoke Example key?",
  });
  expect(confirmation).toHaveTextContent(/gateway.*unreachable/i);
  expect(confirmation).not.toHaveTextContent(
    /rejected right away|immediately disable/i,
  );
  await user.click(
    within(confirmation).getByRole("button", { name: "Revoke key" }),
  );
  expect(state.revoke).toHaveBeenCalledWith("key-uuid", expect.any(Object));
  return state.revoke.mock.calls[0][1];
}
describe("release audit: partial revocation on the v2 action owner", () => {
  it("keeps a gateway warning visible through a detail refetch", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<KeyManagementSection />);
    const callbacks = await revoke(user);
    act(() => callbacks.onSuccess({ status: "revoked", warning }));
    // Clearing the selected key changes the confirmation's title before MUI
    // finishes its exit. Wait for the modal, not just its former name.
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: /^Revoke / })).toBeNull(),
    );
    expect(await screen.findByText(warning)).toBeVisible();
    expect(
      screen.getByText(/Gateway enforcement is not confirmed/),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    state.keys[0] = { ...state.keys[0], status: "revoked" };
    rerender(<KeyManagementSection />);
    expect(screen.getByText(warning)).toBeVisible();
    expect(
      within(screen.getByRole("dialog", { name: "Example key" })).getByText(
        "Revoked locally",
      ),
    ).toBeVisible();
  });
  it("closes normally when revocation returns no warning", async () => {
    render(<KeyManagementSection />);
    const callbacks = await revoke(userEvent.setup());
    act(() => callbacks.onSuccess({ status: "revoked" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Example key" })).toBeNull(),
    );
    expect(screen.queryByText(warning)).toBeNull();
  });
  it("a fresh revoked detail does not claim enforcement", () => {
    state.keys[0].status = "revoked";
    render(<KeyDetailDrawer keyId="key-uuid" open onClose={vi.fn()} />);
    expect(screen.getByText("Revoked locally")).toBeVisible();
    expect(
      screen.getByText(/Gateway enforcement is not confirmed/),
    ).toBeVisible();
  });
  it("a fresh revoked list labels the status locally", () => {
    state.keys[0].status = "revoked";
    render(<KeyManagementSection />);
    expect(
      within(screen.getByRole("table")).getByText("Revoked locally"),
    ).toBeVisible();
  });
  it("does not carry one key's warning into another key", async () => {
    state.keys.push({ ...state.keys[0], id: "other", name: "Other key" });
    render(<KeyManagementSection />);
    const user = userEvent.setup();
    const callbacks = await revoke(user);
    act(() => callbacks.onSuccess({ status: "revoked", warning }));
    // Clearing the selected key changes the confirmation's title before MUI
    // finishes its exit. Wait for the modal, not just its former name.
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: /^Revoke / })).toBeNull(),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Revoke Example key?" }),
      ).toBeNull(),
    );
    await waitFor(() =>
      expect(screen.getByRole("dialog", { name: "Example key" })).toHaveFocus(),
    );
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Example key" })).toBeNull(),
    );
    const drawer = await openKey(user, "Other key");
    expect(screen.queryByText(warning)).toBeNull();
    expect(
      within(drawer).getByRole("button", { name: "Revoke" }),
    ).toBeEnabled();
  });
});
describe("release audit: keyboard key details", () => {
  it.each(["{Enter}", " "])(
    "opens details from tab order with %s",
    async (key) => {
      render(<KeyManagementSection />);
      const user = userEvent.setup();
      const button = screen.getByRole("button", {
        name: "View details for Example key",
      });
      for (let i = 0; i < 20 && document.activeElement !== button; i += 1)
        await user.tab();
      expect(button).toHaveFocus();
      await user.keyboard(key);
      const drawer = await screen.findByRole("dialog", { name: "Example key" });
      expect(
        within(drawer).getByRole("button", { name: "Edit" }),
      ).toBeEnabled();
      expect(
        within(drawer).getByRole("button", { name: "Revoke" }),
      ).toBeEnabled();
    },
  );
  it("lets a member read details without admin writes", async () => {
    state.canEdit = false;
    render(<KeyManagementSection />);
    const user = userEvent.setup();
    screen
      .getByRole("button", { name: "View details for Example key" })
      .focus();
    await user.keyboard("{Enter}");
    const drawer = await screen.findByRole("dialog", { name: "Example key" });
    expect(within(drawer).getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(
      within(drawer).getByRole("button", { name: "Revoke" }),
    ).toBeDisabled();
  });
});
// Exercise the actual column getters/comparators passed to AG Grid. AG Grid
// reverses a comparator's result in descending order, including null handling.
function sorted(field, descending) {
  const column = lastGrid.props.columnDefs.find((c) => c.field === field);
  const value = (data) =>
    column.valueGetter ? column.valueGetter({ data }) : data[field];
  return [...state.models]
    .sort((a, b) => {
      const compared = column.comparator
        ? column.comparator(value(a), value(b), null, null, descending)
        : String(value(a)).localeCompare(String(value(b)));
      return descending ? -compared : compared;
    })
    .map((m) => m.model);
}
describe("release audit: v2 numeric columns", () => {
  it.each([false, true])(
    "keeps Cache hits available with a current-state tooltip (off=%s)",
    (cacheOff) => {
      const { rerender } = render(<ModelComparison cacheOff={cacheOff} />);
      const fields = lastGrid.props.columnDefs.map((column) => column.field);
      expect(fields).toContain("cache_hit_rate");
      const cacheColumn = () =>
        lastGrid.props.columnDefs.find(
          (column) => column.field === "cache_hit_rate",
        );
      expect(cacheColumn().headerTooltip).toBe(
        cacheOff
          ? "Response caching is off on this gateway. Cache hits reflect the selected period."
          : undefined,
      );
      rerender(<ModelComparison cacheOff={!cacheOff} />);
      expect(cacheColumn()).toBeDefined();
      expect(cacheColumn().headerTooltip).toBe(
        !cacheOff
          ? "Response caching is off on this gateway. Cache hits reflect the selected period."
          : undefined,
      );
      expect(fields).toContain("total_cost");
      expect(sorted("total_cost", false)).toEqual([
        "model-fraction",
        "model-nine",
        "model-ten",
      ]);
    },
  );
  it("sorts decimal strings numerically both ways", () => {
    render(<ModelComparison />);
    expect(sorted("total_cost", false)).toEqual([
      "model-fraction",
      "model-nine",
      "model-ten",
    ]);
    expect(sorted("total_cost", true)).toEqual([
      "model-ten",
      "model-nine",
      "model-fraction",
    ]);
  });
  it("keeps missing costs last and accepts mixed numeric types", () => {
    state.models[0].total_cost = 9;
    state.models.push({
      model: "model-missing",
      provider: "omega",
      total_cost: null,
    });
    render(<ModelComparison />);
    expect(sorted("total_cost", false)).toEqual([
      "model-fraction",
      "model-nine",
      "model-ten",
      "model-missing",
    ]);
    expect(sorted("total_cost", true)).toEqual([
      "model-ten",
      "model-nine",
      "model-fraction",
      "model-missing",
    ]);
  });
  it("preserves provider-name sorting", () => {
    render(<ModelComparison />);
    expect(sorted("provider", false)).toEqual([
      "model-ten",
      "model-fraction",
      "model-nine",
    ]);
  });
});
