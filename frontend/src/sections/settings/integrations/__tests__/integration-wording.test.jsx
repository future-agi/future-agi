import React from "react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";

import { PLATFORMS } from "../constants";
import IntegrationsList from "../IntegrationsList";

// The Integrations page describes what its cards do: some export request
// logs and metrics, some send alerts or issues. The product is
// named "AI Gateway" wherever a card refers to it.

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

vi.mock("../AddIntegrationWizard", () => ({ default: () => null }));

vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({
    allowed: false,
    reasonCode: null,
    isLoading: false,
    isError: false,
  }),
}));

const renderList = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <IntegrationsList />
    </QueryClientProvider>,
  );

describe("Integrations page wording", () => {
  it("says the page connects external platforms", () => {
    renderList();
    expect(screen.getByText("Connect external platforms")).toBeInTheDocument();
    expect(
      screen.queryByText(/observability platforms to import traces/i),
    ).toBeNull();
  });

  it("describes export and alerts when nothing is connected", () => {
    renderList();
    expect(
      screen.getByText(
        "Export request logs and metrics, or send alerts to an external platform.",
      ),
    ).toBeInTheDocument();
  });

  it("names the product AI Gateway in every card", () => {
    PLATFORMS.forEach((platform) => {
      expect(platform.description).not.toMatch(/agent command center/i);
      expect(platform.wizardDescription).not.toMatch(/agent command center/i);
    });
    const byId = Object.fromEntries(PLATFORMS.map((p) => [p.id, p]));
    expect(byId.datadog.wizardDescription).toBe(
      "Export AI Gateway request logs and metrics to Datadog",
    );
    expect(byId.pagerduty.wizardDescription).toBe(
      "Route AI Gateway alerts to PagerDuty incidents",
    );
  });

  it("promises on the Datadog card only what the export sends", () => {
    const datadog = PLATFORMS.find((p) => p.id === "datadog");
    expect(datadog.description).toBe("AI Gateway request logs and metrics");
    expect(datadog.description).not.toMatch(/trace|APM/i);
  });

  // Each sends one agentcc_request event per request log, with the model,
  // provider, status, tokens, cost and time, and no user's identity.
  it.each([
    ["posthog", "AI Gateway request logs as PostHog events"],
    ["mixpanel", "AI Gateway request logs as Mixpanel events"],
  ])(
    "promises on the %s card the events the export sends",
    (id, description) => {
      const platform = PLATFORMS.find((p) => p.id === id);
      expect(platform.description).toBe(description);
      expect(platform.description).not.toMatch(/user|tracking|analytics/i);
    },
  );
});
