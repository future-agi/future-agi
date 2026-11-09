import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, renderWithRouter, screen } from "src/utils/test-utils";

import IntegrationsList from "../IntegrationsList";
import StatusBadge from "../StatusBadge";
import MixpanelCredentials from "../AddIntegrationWizard/credentials/MixpanelCredentials";

// What the Integrations pages say matches what happened: a Slack link a
// Member cannot follow is a warning, not a success; "Slack workspace
// connected successfully." holds only while Slack is connected; an export's
// first sync reads "First export", not the import word "Backfilling"; and
// Mixpanel's credentials step says its check sends test events.

const h = vi.hoisted(() => ({ role: "Owner", connections: [] }));

vi.mock("src/api/integrations", () => ({
  integrationKeys: { connections: () => ["integrations", "connections"] },
  useIntegrationConnections: () => ({
    data: h.connections,
    isLoading: false,
    isError: false,
  }),
  useStartSlackInstall: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteConnection: () => ({ mutate: vi.fn(), isPending: false }),
  useValidateCredentials: () => ({
    mutate: vi.fn(),
    isPending: false,
    error: null,
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: h.role }),
}));
vi.mock("../AddIntegrationWizard", () => ({ default: () => null }));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
}));

const SLACK = {
  id: "conn-slack",
  platform: "slack",
  display_name: "Acme Slack",
  host_url: "https://slack.com",
  created_at: "2026-10-20T10:00:00Z",
};

const page = (route) =>
  renderWithRouter(
    <QueryClientProvider client={new QueryClient()}>
      <IntegrationsList />
    </QueryClientProvider>,
    { route },
  );

beforeEach(() => {
  h.role = "Owner";
  h.connections = [];
});

describe("Integrations notices", () => {
  it("a Slack link a Member cannot follow is a warning", () => {
    h.role = "Member";
    page("/dashboard/settings/integrations?platform=slack");
    const alert = screen
      .getByText(
        "Only an Owner or Admin can connect Slack. Ask one to connect it here.",
      )
      .closest(".MuiAlert-root");
    expect(alert).toHaveClass("MuiAlert-standardWarning");
  });

  it("says Slack is connected while it is, and not after a Disconnect", () => {
    h.connections = [SLACK];
    const { rerender } = page(
      "/dashboard/settings/integrations?slack=connected",
    );
    expect(
      screen.getByText("Slack workspace connected successfully."),
    ).toBeInTheDocument();
    h.connections = [];
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <IntegrationsList />
      </QueryClientProvider>,
    );
    expect(
      screen.queryByText("Slack workspace connected successfully."),
    ).toBeNull();
    expect(screen.getByText("No integrations connected")).toBeInTheDocument();
  });
});

describe("Status badge", () => {
  it.each([
    [true, "First export"],
    [false, "Backfilling"],
  ])("an export (%s) first sync reads %s", (isExport, label) => {
    render(<StatusBadge status="backfilling" isExport={isExport} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});

describe("Mixpanel's credentials step", () => {
  it("says its check sends test events to the project", () => {
    render(
      <MixpanelCredentials
        data={{}}
        onUpdate={vi.fn()}
        onNext={vi.fn()}
        onBack={vi.fn()}
      />,
    );
    expect(
      screen.getByText(
        "Validate & Continue, and connecting, each send one $agentcc_connection_test event to the project to check the credentials.",
      ),
    ).toBeInTheDocument();
  });
});
