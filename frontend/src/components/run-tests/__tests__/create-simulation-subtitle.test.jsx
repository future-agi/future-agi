import React from "react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderWithRouter, screen } from "src/utils/test-utils";

import CreateRunTestPage from "../CreateRunTestPage";

// The Run Simulation list reads "Run your agent through
// scenarios at scale and score every conversation with your evals." while
// the create page under the same title read "Create and manage comprehensive
// tests for your AI agents with scenarios, evaluations, and automated runs."

// Same mocks as scenario-step-loading.test.jsx. The hook return values must
// be referentially stable: an effect writes the first version back into form
// state, so a fresh object per render loops forever.
const mocks = vi.hoisted(() => ({
  get: vi.fn(() => Promise.resolve({ data: {} })),
  post: vi.fn(() => Promise.resolve({ data: {} })),
  agentDefinitions: {
    // AGENT_TYPES.CHAT is "text".
    agentDefinitions: [
      { id: "agent-1", agent_name: "Support Agent", agent_type: "text" },
    ],
    fetchNextPage: () => {},
    isLoading: false,
    isFetchingNextPage: false,
  },
  agentVersionsEmpty: {
    data: { pages: [] },
    isLoading: false,
    fetchNextPage: () => {},
    isFetchingNextPage: false,
  },
  agentVersionsLoaded: {
    data: {
      pages: [{ results: [{ id: "version-1", version_name_display: "v1" }] }],
    },
    isLoading: false,
    fetchNextPage: () => {},
    isFetchingNextPage: false,
  },
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    scenarios: {
      list: "/scenarios/",
      getColumns: "/scenarios/columns/",
    },
  },
}));

vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));

vi.mock("../common", async (importOriginal) => ({
  ...(await importOriginal()),
  useAgentDefinitions: () => mocks.agentDefinitions,
}));

vi.mock("src/api/agent-definition/agent-definition-version", () => ({
  useAgentDefinitionVersions: ({ selectedAgentId }) =>
    selectedAgentId ? mocks.agentVersionsLoaded : mocks.agentVersionsEmpty,
}));

const renderPage = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return renderWithRouter(
    <QueryClientProvider client={queryClient}>
      <CreateRunTestPage open onClose={vi.fn()} />
    </QueryClientProvider>,
  );
};

describe("Run Simulation create page header", () => {
  it("says what this page does, in the same voice as the Run Simulation list", () => {
    renderPage();

    expect(
      screen.getByText(
        "Choose your agent, the scenarios to run it through, and the evals that score every conversation.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/comprehensive tests/i)).toBeNull();
  });
});
