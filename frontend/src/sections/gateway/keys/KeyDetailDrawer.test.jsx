import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import KeyDetailDrawer from "./KeyDetailDrawer";

const { keyState, analyticsState, overviewCalls, usageCalls } = vi.hoisted(
  () => ({
    keyState: { data: null },
    analyticsState: { isError: false },
    overviewCalls: [],
    usageCalls: [],
  }),
);

vi.mock("./hooks/useApiKeys", () => ({
  useApiKeyDetail: () => ({ data: keyState.data, isLoading: false }),
  useRevokeApiKey: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateApiKey: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
}));

vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: (params, options) => {
    overviewCalls.push({ params, options });
    return { data: undefined, isError: analyticsState.isError };
  },
}));

vi.mock("../analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: (params, options) => {
    usageCalls.push({ params, options });
    return { data: undefined, isError: analyticsState.isError };
  },
}));

const renderDrawer = () =>
  render(
    <KeyDetailDrawer
      keyId="key-uuid"
      open
      onClose={vi.fn()}
      gatewayId="gw-1"
    />,
  );

describe("KeyDetailDrawer per-key analytics", () => {
  beforeEach(() => {
    overviewCalls.length = 0;
    usageCalls.length = 0;
    analyticsState.isError = false;
    keyState.data = {
      id: "0f1b1f3e-0000-4000-8000-000000000001",
      name: "prod key",
      status: "active",
      // What the gateway stamps on a request log. Not a UUID, and the only
      // value `api_key_id` can be filtered on.
      gatewayKeyId: "gw-key-7f3a",
      allowedModels: [],
      allowedProviders: [],
    };
  });

  it("filters by the gateway key id, not the key's UUID primary key", () => {
    renderDrawer();

    expect(overviewCalls.at(-1).params.apiKeyId).toBe("gw-key-7f3a");
    expect(usageCalls.at(-1).params.apiKeyId).toBe("gw-key-7f3a");
  });

  it("keeps these ambient reads off the app-wide error toast", () => {
    // Otherwise a failed refetch lands next to a save the user just made and
    // reads as "the save failed".
    renderDrawer();

    expect(overviewCalls.at(-1).options.meta).toEqual({ errorHandled: true });
    expect(usageCalls.at(-1).options.meta).toEqual({ errorHandled: true });
  });

  it("reports a failed usage read in the panel it belongs to", () => {
    analyticsState.isError = true;

    renderDrawer();

    expect(
      screen.getByText(/Usage for this key could not be loaded/i),
    ).toBeInTheDocument();
  });

  it("says nothing when the usage read succeeded", () => {
    renderDrawer();

    expect(
      screen.queryByText(/Usage for this key could not be loaded/i),
    ).not.toBeInTheDocument();
  });
});
