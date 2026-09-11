import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  renderWithRouter,
  screen,
  waitFor,
  fireEvent,
} from "src/utils/test-utils";

import CreateRunTestPage from "../CreateRunTestPage";

// The hook return values below must be referentially stable: `versionOptions`
// is a useMemo over the hook's `data`, and an effect writes the first version
// back into form state, so a fresh object per render loops forever.
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  agentDefinitions: {
    agentDefinitions: [
      { id: "agent-1", agent_name: "Support Agent", agent_type: "chat" },
    ],
    fetchNextPage: () => {},
    isLoading: false,
    isFetchingNextPage: false,
  },
  // Picking an agent clears the version, and only a *new* `versionOptions`
  // identity re-triggers the effect that auto-selects one — so these two
  // results must be distinct objects, each stable.
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

const scenarioPage = (page, { count = 30, size = 10 } = {}) => ({
  count,
  results: Array.from({ length: size }, (_, i) => ({
    id: `p${page}-s${i}`,
    name: `Scenario ${page}-${i}`,
    description: "desc",
    dataset_rows: 5,
  })),
});

// Resolves the scenarios request only when we say so, so the assertion lands
// while the page-2 fetch is genuinely in flight.
let releaseScenarios;
const deferScenarios = () =>
  new Promise((resolve) => {
    releaseScenarios = resolve;
  });

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

// Step 0 gates on name + agent definition + version; fill it to reach the
// scenario chooser.
const goToScenarioStep = async () => {
  fireEvent.change(screen.getByPlaceholderText(/Enter a name for your/i), {
    target: { value: "Run A" },
  });

  fireEvent.click(
    screen.getByPlaceholderText(/Choose your agent that you want to test/i),
  );
  fireEvent.click(await screen.findByText("Support Agent"));

  // An effect auto-selects the first version; Next unlocks once it lands.
  const next = screen.getByRole("button", { name: /next/i });
  await waitFor(() => expect(next).toBeEnabled());
  fireEvent.click(next);
  await screen.findByText("Choose your scenarios");
};

describe("Choose Scenarios step — paging", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    releaseScenarios = undefined;
  });

  it("shows the loader, not the empty state, while the next page is fetching", async () => {
    mocks.get.mockImplementation((url) => {
      if (url === "/scenarios/")
        return Promise.resolve({ data: scenarioPage(1) });
      return Promise.resolve({ data: {} });
    });

    renderPage();
    await goToScenarioStep();
    expect(await screen.findByText("Scenario 1-0")).toBeInTheDocument();

    // Page 2 hangs until we release it.
    mocks.get.mockImplementation((url) => {
      if (url === "/scenarios/") return deferScenarios();
      return Promise.resolve({ data: {} });
    });

    fireEvent.click(screen.getByTitle(/Go to next page/i));

    // The in-flight fetch empties the list; the step must not read that as
    // "this workspace has no scenarios".
    await waitFor(() =>
      expect(screen.getByRole("progressbar")).toBeInTheDocument(),
    );
    expect(
      screen.queryByText("Add your first scenario"),
    ).not.toBeInTheDocument();

    releaseScenarios({ data: scenarioPage(2) });
    expect(await screen.findByText("Scenario 2-0")).toBeInTheDocument();
  });

  it("keeps the rows-per-page control when one page covers every scenario", async () => {
    // Raising the page size past the total used to hide the whole pagination
    // bar — including the selector that raised it, stranding the user there.
    mocks.get.mockImplementation((url) => {
      if (url === "/scenarios/")
        return Promise.resolve({
          data: scenarioPage(1, { count: 6, size: 6 }),
        });
      return Promise.resolve({ data: {} });
    });

    renderPage();
    await goToScenarioStep();
    expect(await screen.findByText("Scenario 1-0")).toBeInTheDocument();

    expect(screen.getByText(/rows per page/i)).toBeInTheDocument();
  });

  // Guards the fix from over-correcting: the empty state must still appear
  // once the fetch settles with nothing. Driven inline rather than through
  // `goToScenarioStep`, which waits on a heading this case never renders.
  it("still shows the empty state when the workspace really has no scenarios", async () => {
    mocks.get.mockImplementation((url) => {
      if (url === "/scenarios/")
        return Promise.resolve({ data: { count: 0, results: [] } });
      return Promise.resolve({ data: {} });
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText(/Enter a name for your/i), {
      target: { value: "Run A" },
    });
    fireEvent.click(
      screen.getByPlaceholderText(/Choose your agent that you want to test/i),
    );
    fireEvent.click(await screen.findByText("Support Agent"));
    const next = screen.getByRole("button", { name: /next/i });
    await waitFor(() => expect(next).toBeEnabled());
    fireEvent.click(next);

    expect(
      await screen.findByText("Add your first scenario"),
    ).toBeInTheDocument();
  });
});
