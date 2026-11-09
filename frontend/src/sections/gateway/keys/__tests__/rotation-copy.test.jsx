import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, userEvent, within } from "src/utils/test-utils";
import { renderPage } from "../../guardrails/__tests__/helpers";
import KeyManagementSection from "../KeyManagementSection";
import approvals from "./rotation-copy.activation.json";

const fixture = vi.hoisted(() => ({
  result: null,
  error: null,
  snackbar: vi.fn(),
  key: {
    id: "old-row", name: "Rotation fixture", user: "actor",
    organization: "org-a", status: "active", allowedModels: [], allowedProviders: [],
  },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8, currentOrganizationId: "org-a", isReady: true }),
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ user: { id: "actor" } }) }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: fixture.snackbar }));
vi.mock("ag-grid-react", async () => await import("../../providers/__tests__/grid-mock"));
vi.mock("react-apexcharts", () => ({ default: () => null }));
vi.mock("../../providers/ui/CreditBalanceChip", () => ({ default: () => null }));
vi.mock("../CreateKeyDialog", () => ({ default: () => null }));
vi.mock("../EditKeyDialog", () => ({ default: () => null }));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "fixture-gateway" }),
}));
vi.mock("../hooks/useApiKeys", () => ({
  useApiKeys: () => ({ data: [fixture.key], isLoading: false, isError: false }),
  useApiKeyDetail: () => ({ data: fixture.key, isLoading: false }),
  useRevokeApiKey: () => ({ mutate: vi.fn(), isPending: false }),
  useSyncApiKeys: () => ({ mutate: vi.fn(), isPending: false }),
  useRotateApiKey: () => ({
    isPending: false,
    reset: vi.fn(),
    mutate: (_, callbacks) => fixture.error
      ? callbacks.onError(fixture.error)
      : callbacks.onSuccess(fixture.result),
  }),
}));
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: undefined, isError: false }),
}));
vi.mock("../../analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: () => ({ data: undefined, isError: false }),
}));

const exact = (id) => approvals.rows[`KEYS-ROTATE-${id}`].afterExact;
const bytes = (value) => Array.from(new TextEncoder().encode(value));
function assertRendered(scope, expected) {
  // textContent equality checks whitespace too; getByText alone normalizes it.
  const node = within(scope).getByText(expected, { exact: true });
  expect(node.textContent).toBe(expected);
  expect(bytes(node.textContent)).toEqual(bytes(expected));
}

beforeEach(() => {
  vi.clearAllMocks();
  fixture.error = null;
  fixture.result = {
    secret: "fixture-once", created: { id: "new-row", name: fixture.key.name },
    oldRevoked: true, gatewayRevoked: false, expiryCopied: true, revokeWarning: null,
  };
});

async function openConfirmation(user) {
  await user.click(screen.getByRole("button", { name: "Actions for Rotation fixture" }));
  await user.click(screen.getByRole("menuitem", { name: "Rotate" }));
  return screen.findByRole("dialog", { name: "Rotate Rotation fixture?" });
}

async function reveal(user) {
  const confirm = await openConfirmation(user);
  await user.click(within(confirm).getByRole("button", { name: "Rotate key" }));
  return screen.findByRole("dialog", { name: "Copy your new key" });
}

describe("activated rotation copy, byte for byte", () => {
  it("renders KEYS-ROTATE-1, not the proposed show-then-revoke placeholder", async () => {
    const user = userEvent.setup();
    renderPage(<KeyManagementSection />);
    assertRendered(await openConfirmation(user), exact(1));
  });

  it("renders KEYS-ROTATE-7 alongside the separate enforcement state", async () => {
    const user = userEvent.setup();
    renderPage(<KeyManagementSection />);
    assertRendered(await reveal(user), exact(7).replace("${name}", fixture.key.name));
  });

  it("renders KEYS-ROTATE-8 without assuming a rejected revoke left the key active", async () => {
    fixture.result.oldRevoked = false;
    const user = userEvent.setup();
    renderPage(<KeyManagementSection />);
    assertRendered(await reveal(user), exact(8));
  });

  it("renders KEYS-ROTATE-9 without inferring missing expiry from response loss", async () => {
    fixture.result.expiryCopied = false;
    const user = userEvent.setup();
    renderPage(<KeyManagementSection />);
    assertRendered(await reveal(user), exact(9));
  });

  it("renders the joined KEYS-ROTATE-5 API warning unchanged", async () => {
    fixture.result.revokeWarning = approvals.rows["KEYS-ROTATE-5"].renderedExact;
    const user = userEvent.setup();
    renderPage(<KeyManagementSection />);
    assertRendered(await reveal(user), approvals.rows["KEYS-ROTATE-5"].renderedExact);
  });

  it.each(["", "Fixture failure"])("uses exact KEYS-ROTATE-10 snackbar copy (%s)", async (message) => {
    fixture.error = new Error(message);
    const user = userEvent.setup();
    renderPage(<KeyManagementSection />);
    const confirm = await openConfirmation(user);
    await user.click(within(confirm).getByRole("button", { name: "Rotate key" }));
    const expected = exact(10).replace(
      '${err?.message || "the new key could not be retrieved"}',
      message || "the new key could not be retrieved",
    );
    expect(fixture.snackbar).toHaveBeenCalledExactlyOnceWith(expected, { variant: "error" });
    expect(bytes(fixture.snackbar.mock.calls[0][0])).toEqual(bytes(expected));
  });
});
