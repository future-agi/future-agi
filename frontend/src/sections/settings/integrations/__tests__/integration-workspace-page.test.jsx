import React from "react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { render, screen } from "src/utils/test-utils";

import WorkspaceIntegrations from "src/pages/dashboard/settings/WorkspaceSettings/WorkspaceIntegrations";

// The workspace's Settings > Integrations page is the Integrations page: one
// "Integrations" heading and its one line, as on the organization's page.

vi.mock("src/api/integrations", () => ({
  integrationKeys: { connections: () => ["integrations", "connections"] },
  useIntegrationConnections: () => ({
    data: [],
    isLoading: false,
    isError: false,
  }),
  useStartSlackInstall: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

vi.mock("src/sections/settings/integrations/AddIntegrationWizard", () => ({
  default: () => null,
}));

vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({
    allowed: false,
    reasonCode: null,
    isLoading: false,
    isError: false,
  }),
}));

describe("Workspace Integrations page", () => {
  it("has one heading and one line under it", () => {
    render(
      <HelmetProvider>
        <QueryClientProvider client={new QueryClient()}>
          <WorkspaceIntegrations />
        </QueryClientProvider>
      </HelmetProvider>,
    );
    expect(screen.getAllByText("Integrations", { exact: true })).toHaveLength(
      1,
    );
    expect(screen.getByText("Connect external platforms")).toBeInTheDocument();
    expect(screen.queryByText("Manage workspace integrations")).toBeNull();
  });
});
