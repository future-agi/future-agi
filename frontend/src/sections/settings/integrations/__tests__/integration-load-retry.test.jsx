import React from "react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within, fireEvent } from "src/utils/test-utils";

import IntegrationsList from "../IntegrationsList";
import IntegrationSyncHistory from "../IntegrationSyncHistory";

// W10-SETTINGS-TASKS-U03: a failed first read is not a dead end. The
// Integrations page keeps its header (Add Integration) and Webhooks and shows
// the failure where the connections go, with a Retry; Sync history offers the
// same Retry.

const h = vi.hoisted(() => ({
  refetchConnections: () => {},
  refetchSyncLogs: () => {},
  isRefetching: false,
}));

vi.mock("src/api/integrations", () => ({
  integrationKeys: { connections: () => ["integrations", "connections"] },
  useIntegrationConnections: () => ({
    data: undefined,
    isLoading: false,
    isError: true,
    isRefetching: h.isRefetching,
    refetch: h.refetchConnections,
  }),
  useStartSlackInstall: () => ({ mutate: vi.fn(), isPending: false }),
  useSyncLogs: () => ({
    data: undefined,
    isLoading: false,
    isError: true,
    isRefetching: h.isRefetching,
    refetch: h.refetchSyncLogs,
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("../AddIntegrationWizard", () => ({ default: () => null }));
vi.mock("../webhooks/WebhooksSection", () => ({
  default: function WebhooksSectionStub() {
    return <h2>Webhooks</h2>;
  },
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgTheme: () => undefined }));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));

beforeEach(() => {
  h.refetchConnections = vi.fn();
  h.refetchSyncLogs = vi.fn();
  h.isRefetching = false;
  window.history.pushState({}, "", "/");
});

const renderList = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <IntegrationsList />
    </QueryClientProvider>,
  );

describe("Integrations load failure", () => {
  it("keeps Add Integration and Webhooks and offers Retry for the connections", () => {
    renderList();

    const failure = within(screen.getByRole("alert"));
    expect(failure.getByText("Failed to load integrations")).toBeVisible();
    expect(failure.getByText("Check your connection and retry.")).toBeVisible();

    expect(
      screen.getByRole("button", { name: "Add Integration" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("heading", { name: "Webhooks" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("No integrations connected"),
    ).not.toBeInTheDocument();

    const retry = failure.getByRole("button", { name: "Retry" });
    expect(retry).toBeEnabled();
    fireEvent.click(retry);
    expect(h.refetchConnections).toHaveBeenCalledTimes(1);
  });

  it("disables Retry while the connections are read again", () => {
    h.isRefetching = true;
    renderList();

    expect(
      within(screen.getByRole("alert")).getByRole("button", {
        name: "Retrying…",
      }),
    ).toBeDisabled();
  });
});

describe("Sync history load failure", () => {
  it("offers Retry", () => {
    render(
      <IntegrationSyncHistory connectionId="synthetic-sync-error-connection" />,
    );

    const failure = within(screen.getByRole("alert"));
    expect(failure.getByText("Failed to load sync history")).toBeVisible();
    expect(failure.getByText("Check your connection and retry.")).toBeVisible();

    fireEvent.click(failure.getByRole("button", { name: "Retry" }));
    expect(h.refetchSyncLogs).toHaveBeenCalledTimes(1);
  });
});
