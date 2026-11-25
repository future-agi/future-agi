import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import KeyManagementSection from "./KeyManagementSection";
import KeyDetailDrawer from "./KeyDetailDrawer";

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway" }),
}));
vi.mock("./hooks/useApiKeys", () => ({
  useApiKeys: () => ({
    data: [
      {
        id: "dashboard-id",
        name: "Staging key",
        status: "active",
        keyPrefix: "sk-staging",
        allowedModels: [],
      },
    ],
    isLoading: false,
    refetch: vi.fn(),
  }),
  useSyncApiKeys: () => ({ mutate: vi.fn(), isPending: false }),
  useApiKeyDetail: () => ({
    data: {
      id: "dashboard-id",
      name: "Staging key",
      status: "active",
      allowedModels: [],
      allowedProviders: [],
    },
    isLoading: false,
  }),
  useRevokeApiKey: () => ({ mutate: vi.fn() }),
  useUpdateApiKey: () => ({ mutate: vi.fn() }),
}));
vi.mock("../guardrails/hooks/useGuardrailPolicies", async (importOriginal) => ({
  ...(await importOriginal()),
  useGuardrailPolicies: () => ({
    data: [
      {
        id: "1",
        name: "Policy one",
        scope: "key",
        mode: "monitor",
        is_active: true,
        applied_keys: ["dashboard-id"],
      },
      {
        id: "2",
        name: "Policy two",
        scope: "key",
        mode: "enforce",
        is_active: true,
        // Saved before the gateway ran key policies: stored, not sent.
        runs_on_gateway: false,
        applied_keys: ["dashboard-id"],
      },
      {
        id: "3",
        name: "Policy three",
        scope: "key",
        mode: "monitor",
        is_active: true,
        applied_keys: ["dashboard-id"],
      },
      {
        id: "4",
        name: "Other key",
        scope: "key",
        mode: "enforce",
        is_active: true,
        applied_keys: ["other-id"],
      },
    ],
  }),
}));
vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: {} }),
}));
vi.mock("../analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: () => ({ data: {} }),
}));
vi.mock("react-apexcharts", () => ({ default: () => null }));
vi.mock("./CreateKeyDialog", () => ({ default: () => null }));
vi.mock("./EditKeyDialog", () => ({ default: () => null }));

describe("key guardrail policies", () => {
  it("shows two policy chips plus a count on the key list", () => {
    render(<KeyManagementSection />);
    expect(
      screen.getByRole("columnheader", { name: "Guardrail policies" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Policy one")).toBeInTheDocument();
    expect(screen.getByText("Policy two · not running")).toBeInTheDocument();
    expect(screen.getByText("+1")).toBeInTheDocument();
    expect(screen.queryByText("Policy three")).not.toBeInTheDocument();
  });

  it("links assigned policies in the key drawer and notes org-wide guardrails", () => {
    render(
      <KeyDetailDrawer
        keyId="dashboard-id"
        open
        onClose={vi.fn()}
        gatewayId="gateway"
        policies={[
          {
            id: "1",
            name: "Policy one",
            scope: "key",
            mode: "monitor",
            is_active: true,
            applied_keys: ["dashboard-id"],
          },
        ]}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Policy one" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Monitor")).toBeInTheDocument();
    expect(
      screen.getByText("Org-wide guardrails also apply to this key."),
    ).toBeInTheDocument();
  });

  it("marks a dormant policy in the key list and the key drawer", () => {
    render(<KeyManagementSection />);
    expect(screen.getByTestId("key-policy-chip-dormant-2")).toHaveTextContent(
      "Policy two · not running",
    );
    expect(
      screen.queryByTestId("key-policy-chip-dormant-1"),
    ).not.toBeInTheDocument();
  });

  it("shows 'Not running – save to start' for a dormant policy in the drawer", () => {
    render(
      <KeyDetailDrawer
        keyId="dashboard-id"
        open
        onClose={vi.fn()}
        gatewayId="gateway"
        policies={[
          {
            id: "1",
            name: "Policy one",
            scope: "key",
            mode: "enforce",
            is_active: true,
            runs_on_gateway: true,
            applied_keys: ["dashboard-id"],
          },
          {
            id: "2",
            name: "Policy two",
            scope: "key",
            mode: "enforce",
            is_active: true,
            runs_on_gateway: false,
            applied_keys: ["dashboard-id"],
          },
        ]}
      />,
    );
    expect(screen.getByTestId("key-policy-dormant-2")).toHaveTextContent(
      "Not running – save to start",
    );
    expect(
      screen.queryByTestId("key-policy-dormant-1"),
    ).not.toBeInTheDocument();
  });
});
