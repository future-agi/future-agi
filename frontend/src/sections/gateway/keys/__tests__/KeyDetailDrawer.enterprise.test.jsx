import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

import KeyDetailDrawer from "../KeyDetailDrawer";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "signed-in-user" } }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: 8,
    isReady: true,
    currentOrganizationId: "org-a",
  }),
}));
const keyState = vi.hoisted(() => ({ data: null }));
vi.mock("../hooks/useApiKeys", () => ({
  useApiKeyDetail: () => ({
    data: keyState.data,
    error: null,
    isError: false,
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useCreateApiKey: () => ({ mutate: vi.fn() }),
  useRevokeApiKey: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateApiKey: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
}));
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: undefined, isError: false }),
}));
vi.mock("../../analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: () => ({ data: undefined, isError: false }),
}));
vi.mock("react-apexcharts", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const renderDrawer = () =>
  render(
    <KeyDetailDrawer
      keyId="key-uuid"
      open
      onClose={vi.fn()}
      gatewayId="gw-1"
    />,
  );

// The value next to a drawer row's label.
const rowValue = (label) => screen.getByText(label).nextElementSibling;

// The exact instant and its zone.
const EXACT = "[A-Z][a-z]{2} \\d{1,2}, \\d{4}, \\d{2}:\\d{2}:\\d{2} \\S+";

const writeText = vi.fn();

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-20T12:00:00Z"));
  writeText.mockReset();
  writeText.mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
  keyState.data = {
    id: "key-uuid",
    name: "checkout",
    status: "active",
    keyPrefix: "sk-agentcc-4f2a…9c1d",
    gatewayKeyId: "gw-key-7f3a",
    owner: "team-checkout",
    allowedModels: [],
    allowedProviders: [],
    metadata: {},
    created_at: "2026-10-01T12:00:00Z",
    updated_at: "2026-10-18T12:00:00Z",
    last_used_at: "2026-10-20T09:00:00Z",
    expires_at: null,
  };
});
afterEach(() => {
  vi.useRealTimers();
});

// The key drawer read against Portkey's key fields and the bar's audit
// signals: the id other screens use, copyable, and when the key changed.
describe("API key drawer (enterprise pass)", () => {
  it("shows the key ID that Logs filters by, and copies it", () => {
    renderDrawer();
    expect(rowValue("Key ID")).toHaveTextContent("gw-key-7f3a");
    fireEvent.click(screen.getByRole("button", { name: "Copy key ID" }));
    expect(writeText).toHaveBeenCalledWith("gw-key-7f3a");
  });

  it("shows when the key was created and last updated, with the zone", () => {
    renderDrawer();
    expect(rowValue("Created").textContent).toMatch(new RegExp(`^${EXACT}$`));
    expect(rowValue("Updated").textContent).toMatch(new RegExp(`^${EXACT}$`));
  });

  it("shows when the key was last used, exactly and how long ago", () => {
    renderDrawer();
    expect(rowValue("Last used").textContent).toMatch(
      new RegExp(`^${EXACT} · 3 hours ago$`),
    );
  });
});
