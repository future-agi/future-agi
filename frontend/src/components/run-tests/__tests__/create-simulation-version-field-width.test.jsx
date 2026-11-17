import React from "react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderWithRouter, screen } from "src/utils/test-utils";

import CreateRunTestPage from "../CreateRunTestPage";

// Create a Simulation, first step: at a 1024px window the "Choose version"
// field read "Select agent version (eg:". The field is 300px wide by design
// but sat beside the full-width agent field with the default flex-shrink, so
// the row squeezed it to about 200px; its placeholder needs about 230px
// (163px of 12px Inter plus the field's padding and arrow). jsdom has no
// layout, so this pins the styles that decide it.

// Same mocks as create-simulation-subtitle.test.jsx.
const mocks = vi.hoisted(() => ({
  get: vi.fn(() => Promise.resolve({ data: {} })),
  post: vi.fn(() => Promise.resolve({ data: {} })),
  agentDefinitions: {
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

const VERSION_PLACEHOLDER = "Select agent version (eg: v2)";
const AGENT_PLACEHOLDER = "Choose your agent that you want to test";
// What each placeholder needs to show whole: its text in 12px Inter plus
// 64px of padding, arrow and border.
const AGENT_FIELD_MIN_PX = 292;

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

const fieldRoot = (placeholder) =>
  screen.getByPlaceholderText(placeholder).closest(".MuiFormControl-root");

describe("Create a Simulation: the Choose version field", () => {
  it("keeps its 300px so the whole placeholder shows beside the agent field", () => {
    renderPage();

    // The placeholder keeps its example; nothing is shortened away.
    const version = fieldRoot(VERSION_PLACEHOLDER);
    const agent = fieldRoot(AGENT_PLACEHOLDER);
    expect(version).not.toBeNull();
    expect(agent).not.toBeNull();

    // The version field does not shrink below its 300px.
    const versionStyle = getComputedStyle(version);
    expect(versionStyle.width).toBe("300px");
    expect(versionStyle.flexShrink).toBe("0");
    // ... and never grows past its row on a narrow screen.
    expect(versionStyle.maxWidth).toBe("100%");

    // The agent field starts from enough room for its own placeholder and
    // takes the rest of the row; when the row is narrower than both, the
    // version field moves under it instead of squeezing either.
    const agentStyle = getComputedStyle(agent);
    expect(parseFloat(agentStyle.flexBasis)).toBeGreaterThanOrEqual(
      AGENT_FIELD_MIN_PX,
    );
    expect(agentStyle.flexGrow).toBe("1");

    const row = version.parentElement;
    expect(row).toBe(agent.parentElement);
    expect(getComputedStyle(row).flexWrap).toBe("wrap");
  });
});
