import React from "react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";

import { PLATFORMS, offeredPlatforms } from "../constants";
import StepPlatformSelect from "../AddIntegrationWizard/StepPlatformSelect";
import IntegrationsList from "../IntegrationsList";

// The page and the Add Integration wizard list only platforms that connect:
// every card shown opens its credential step or the Slack install.

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

describe("Integration catalog", () => {
  it("lists only platforms that can be connected", () => {
    const ids = PLATFORMS.map((p) => p.id);
    expect(ids).not.toContain("langsmith");
    expect(ids).not.toContain("arize");
    PLATFORMS.forEach((platform) => {
      expect(platform.available).not.toBe(false);
    });
  });

  it("shows no placeholder card on the Integrations page", () => {
    renderList();
    expect(screen.getByText("Slack")).toBeInTheDocument();
    expect(screen.queryAllByText(/coming soon/i)).toHaveLength(0);
    expect(screen.queryAllByText(/langsmith/i)).toHaveLength(0);
    expect(screen.queryAllByText(/arize/i)).toHaveLength(0);
  });

  it("shows no placeholder card in the Add Integration wizard", () => {
    render(
      <StepPlatformSelect
        platforms={offeredPlatforms(() => false)}
        data={{ platform: "" }}
        onUpdate={vi.fn()}
        onNext={vi.fn()}
        onSelectSlack={vi.fn()}
      />,
    );
    expect(screen.getByText("Slack")).toBeInTheDocument();
    expect(screen.queryAllByText(/coming soon/i)).toHaveLength(0);
    expect(screen.queryAllByText(/langsmith/i)).toHaveLength(0);
    expect(screen.queryAllByText(/arize/i)).toHaveLength(0);
  });
});
