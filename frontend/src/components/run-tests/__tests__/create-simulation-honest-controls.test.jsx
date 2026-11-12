/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import {
  render,
  renderWithRouter,
  screen,
  waitFor,
  fireEvent,
} from "src/utils/test-utils";
import TestRunsView from "src/sections/test/TestRuns/TestRunsView";
import { TestDetailContext } from "src/sections/test/context/TestDetailContext";

import CreateRunTestPage from "../CreateRunTestPage";

// Hook return values must stay referentially stable across renders (see
// scenario-step-loading.test.jsx): a new `versionOptions` identity re-runs the
// effect that auto-selects a version.
const mocks = vi.hoisted(() => {
  const agentList = (agentDefinitions) => ({
    agentDefinitions,
    fetchNextPage: () => {},
    isLoading: false,
    isFetchingNextPage: false,
  });
  return {
    get: vi.fn(),
    post: vi.fn(),
    snackbar: vi.fn(),
    agentDefinitions: null,
    noAgents: agentList([]),
    // What useAgentDefinitions gives while the first page is in flight, and
    // after it failed: no list at all, which is not an empty list.
    agentsLoading: {
      agentDefinitions: undefined,
      fetchNextPage: () => {},
      isLoading: true,
      isFetchingNextPage: false,
    },
    agentsFailed: {
      agentDefinitions: undefined,
      fetchNextPage: () => {},
      isLoading: false,
      isFetchingNextPage: false,
    },
    // AGENT_TYPES.CHAT is "text".
    chatAgent: agentList([
      { id: "agent-1", agent_name: "Support Agent", agent_type: "text" },
    ]),
    // More agents than the pages loaded so far hold: the hook reports a next
    // page (it loads 10 agents a page).
    chatAgentMoreToLoad: {
      ...agentList([
        { id: "agent-1", agent_name: "Support Agent", agent_type: "text" },
      ]),
      hasNextPage: true,
    },
    voiceAgent: agentList([
      { id: "agent-1", agent_name: "Support Agent", agent_type: "voice" },
    ]),
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
  };
});

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    scenarios: {
      list: "/scenarios/",
      getColumns: "/scenarios/columns/",
    },
    agentDefinitions: {
      versionDetail: (agentId, versionId) =>
        `/simulate/agent-definitions/${agentId}/versions/${versionId}/`,
    },
    runTests: {
      create: "/simulate/run-tests/create/",
      runTest: (id) => `/simulate/run-tests/${id}/execute/`,
      getVoiceSDKCode: (id) => `/simulate/run-tests/${id}/sdk-code/`,
    },
  },
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({
    enqueueSnackbar: mocks.snackbar,
    closeSnackbar: () => {},
  }),
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

// The real picker is a multi-step drawer; this stand-in hands back one
// evaluation so the flow can reach the Summary step.
vi.mock("src/sections/common/EvalPicker", () => ({
  serializeEvalConfig: () => ({ config: {} }),
  EvalPickerDrawer: ({ open, onEvalAdded }) =>
    open ? (
      <button
        type="button"
        onClick={() =>
          onEvalAdded({ templateId: "tpl-1", name: "Tone check", mapping: {} })
        }
      >
        Pick mock evaluation
      </button>
    ) : null,
}));

// The page creation navigates to (TestRunsView) is rendered for real; only
// its live parts are stood in for. The header's run button label is pinned in
// sections/test/TestRuns/__tests__/run-button-honest-copy.test.jsx, so here a
// marker only shows whether the header is on the page at all.
vi.mock("src/sections/test/TestRuns/TestRunHeader", () => ({
  default: () => <div data-testid="test-run-header" />,
}));
vi.mock("src/sections/test/TestRuns/TestRunsGrid", () => ({
  default: () => null,
}));
vi.mock("src/hooks/use-simulation-socket", () => ({
  useSimulationSocket: () => {},
}));
vi.mock("react-syntax-highlighter", async (importOriginal) => ({
  ...(await importOriginal()),
  Prism: ({ children }) => <pre>{children}</pre>,
}));

// Mirrors the live /simulate/run-tests/{id}/sdk-code/ response (snake_case).
const sdkCode = {
  installation_guide: "pip install futureagi",
  run_test_id: "sim-1",
  sdk_code: "SDK_RUN_MARKER",
};

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

// The runs page at the URL creation navigated to, before any execution exists:
// test data as simulate/serializers/run_test.py returns it (the agent type is
// pinned on the version snapshot) and zero executions, as TestDetailView
// provides them. Rendered without pushing a route, so it reads the URL the
// creation flow left.
const renderRunsPageAfterCreate = (agentType) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TestDetailContext.Provider
        value={{
          testData: {
            id: "sim-1",
            source_type: "agent_definition",
            agent_version: {
              configuration_snapshot: { agent_type: agentType },
            },
          },
          executionsCount: 0,
          refreshTestRunGrid: () => {},
        }}
      >
        <Routes>
          <Route
            path="/dashboard/simulate/test/:testId/runs"
            element={<TestRunsView />}
          />
        </Routes>
      </TestDetailContext.Provider>
    </QueryClientProvider>,
  );
};

const nextButton = () => screen.getByRole("button", { name: /^next$/i });

// Walks every step with one agent, one scenario and one evaluation, and
// returns the final button on Summary.
const walkToSummary = async () => {
  fireEvent.change(screen.getByPlaceholderText(/Enter a name for your/i), {
    target: { value: "Run A" },
  });
  fireEvent.click(
    screen.getByPlaceholderText(/Choose your agent that you want to test/i),
  );
  fireEvent.click(await screen.findByText("Support Agent"));
  await waitFor(() => expect(nextButton()).toBeEnabled());
  fireEvent.click(nextButton());

  await screen.findByText("Choose your scenarios");
  fireEvent.click(await screen.findByText("Refund request"));
  await waitFor(() => expect(nextButton()).toBeEnabled());
  fireEvent.click(nextButton());

  fireEvent.click(
    await screen.findByRole("button", { name: /add evaluations/i }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Pick mock evaluation" }),
  );
  await waitFor(() => expect(nextButton()).toBeEnabled());
  fireEvent.click(nextButton());

  return screen.findByRole("button", { name: /^create simulation$/i });
};

beforeEach(() => {
  vi.clearAllMocks();
  window.history.pushState({}, "", "/");
  vi.spyOn(window, "open").mockImplementation(() => null);
  mocks.agentDefinitions = mocks.chatAgent;
  mocks.get.mockImplementation((url) => {
    if (url === "/scenarios/")
      return Promise.resolve({
        data: {
          count: 1,
          results: [
            {
              id: "scenario-1",
              name: "Refund request",
              description: "A customer asks for a refund",
              dataset_rows: 5,
            },
          ],
        },
      });
    if (url === "/simulate/run-tests/sim-1/sdk-code/")
      return Promise.resolve({ data: { result: sdkCode } });
    return Promise.resolve({ data: {} });
  });
  mocks.post.mockResolvedValue({ data: { id: "sim-1" } });
});

afterEach(() => {
  window.open.mockRestore?.();
});

const NO_AGENTS_LINE = "No agents yet. Connect one below to run a simulation.";
const NO_MATCH_LINE = "No agent matches your search.";
const NO_LOADED_MATCH_LINE =
  "No loaded agent matches. Clear the search, scroll for more.";
const OLD_EMPTY_LINE = "You have not created any agent definition";

const openAgentPicker = () =>
  fireEvent.click(
    screen.getByPlaceholderText(/Choose your agent that you want to test/i),
  );

// Types into the open Choose Agent definition field, which filters its list.
const searchAgents = (text) =>
  fireEvent.change(
    screen.getByPlaceholderText(/Choose your agent that you want to test/i),
    { target: { value: text } },
  );

describe("Create a Simulation: agent link", () => {
  // Before: "Define new agent" pushed /dashboard/simulate/agent-definitions,
  // which redirects to Agents > Connected agents, a list with no form. Agents
  // are connected on Simulate > Environment (the same place Connected agents'
  // own connect button opens), and they then show up here.
  // Before (this row's review): 'Connect an agent' opened that page in this
  // tab, so the name and description typed here were lost. Like Add Scenario
  // on the next step, it now opens a new tab and the wizard stays as it was.
  it("opens Environment's connect tab in a new tab and keeps what was typed", async () => {
    mocks.agentDefinitions = mocks.noAgents;
    renderPage();

    fireEvent.change(screen.getByPlaceholderText(/Enter a name for your/i), {
      target: { value: "Run A" },
    });
    fireEvent.change(
      screen.getByPlaceholderText(/Describe what this simulation run/i),
      { target: { value: "Checks refunds" } },
    );
    openAgentPicker();
    fireEvent.click(await screen.findByText(/Connect an agent/));

    expect(window.open).toHaveBeenCalledWith(
      "/dashboard/simulate/environments?tab=build",
      "_blank",
    );
    expect(window.location.pathname).toBe("/");
    expect(screen.getByDisplayValue("Run A")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Checks refunds")).toBeInTheDocument();
    expect(screen.queryByText(/Define new agent/)).not.toBeInTheDocument();
  });
});

describe("Create a Simulation: no agents yet", () => {
  // Before: in a workspace with no agents, the open Choose Agent definition
  // list read only "No option provided", the picker's generic line, above
  // the Connect an agent link.
  it("says the workspace has no agents, above the Connect an agent link", async () => {
    mocks.agentDefinitions = mocks.noAgents;
    renderPage();

    openAgentPicker();
    // Wait for the open list: its one line, whatever it says, and the
    // Connect an agent link, which is a menu item too.
    await screen.findAllByRole("menuitem");

    expect(
      screen.queryByRole("menuitem", { name: "No option provided" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: NO_AGENTS_LINE }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Connect an agent/)).toBeInTheDocument();
  });

  it("lists the agents as before when the workspace has one", async () => {
    mocks.agentDefinitions = mocks.chatAgent;
    renderPage();

    openAgentPicker();

    expect(await screen.findByText("Support Agent")).toBeInTheDocument();
    expect(screen.queryByText(NO_AGENTS_LINE)).not.toBeInTheDocument();
  });

  // A list that has not arrived says nothing about whether agents exist.
  it.each([
    ["is still loading", "agentsLoading"],
    ["failed to load", "agentsFailed"],
  ])("does not say 'No agents yet' while the list %s", async (_, state) => {
    mocks.agentDefinitions = mocks[state];
    renderPage();

    openAgentPicker();
    await screen.findAllByRole("menuitem");

    expect(screen.queryByText(NO_AGENTS_LINE)).not.toBeInTheDocument();
  });
});

describe("Create a Simulation: agent search finds nothing", () => {
  // Before: a search with no match read "You have not created any agent
  // definition", also in workspaces that have agents, which could lead
  // someone to connect an agent they already have.
  it("says no agent matches, not that the workspace has none", async () => {
    mocks.agentDefinitions = mocks.chatAgent;
    renderPage();

    openAgentPicker();
    await screen.findByText("Support Agent");
    searchAgents("Billing");

    expect(
      await screen.findByRole("menuitem", { name: NO_MATCH_LINE }),
    ).toBeInTheDocument();
    expect(screen.queryByText(OLD_EMPTY_LINE)).not.toBeInTheDocument();
    expect(screen.queryByText("Support Agent")).not.toBeInTheDocument();
  });

  // The search only looks through the agents loaded so far, so a match may
  // sit on a page that has not loaded yet.
  it("says only the loaded agents were searched when more are left to load", async () => {
    mocks.agentDefinitions = mocks.chatAgentMoreToLoad;
    renderPage();

    openAgentPicker();
    await screen.findByText("Support Agent");
    searchAgents("Billing");

    expect(
      await screen.findByRole("menuitem", { name: NO_LOADED_MATCH_LINE }),
    ).toBeInTheDocument();
    expect(screen.queryByText(NO_MATCH_LINE)).not.toBeInTheDocument();
    expect(screen.queryByText(OLD_EMPTY_LINE)).not.toBeInTheDocument();
  });

  it("keeps the no-agents line when the workspace has none", async () => {
    mocks.agentDefinitions = mocks.noAgents;
    renderPage();

    openAgentPicker();
    await screen.findAllByRole("menuitem");
    searchAgents("Billing");

    expect(
      await screen.findByRole("menuitem", { name: NO_AGENTS_LINE }),
    ).toBeInTheDocument();
    expect(screen.queryByText(NO_MATCH_LINE)).not.toBeInTheDocument();
    expect(screen.queryByText(OLD_EMPTY_LINE)).not.toBeInTheDocument();
  });

  // A list that has not arrived holds no agents to match, and says nothing
  // about the workspace either way.
  it.each([
    ["is still loading", "agentsLoading"],
    ["failed to load", "agentsFailed"],
  ])("makes no claim about agents while the list %s", async (_, state) => {
    mocks.agentDefinitions = mocks[state];
    renderPage();

    openAgentPicker();
    await screen.findAllByRole("menuitem");
    searchAgents("Billing");

    expect(
      await screen.findByRole("menuitem", { name: "No option found" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(NO_AGENTS_LINE)).not.toBeInTheDocument();
    expect(screen.queryByText(NO_MATCH_LINE)).not.toBeInTheDocument();
    expect(screen.queryByText(OLD_EMPTY_LINE)).not.toBeInTheDocument();
  });
});

describe("Create a Simulation: final button", () => {
  // Before: the last button read "Run Simulation" but only created the
  // simulation; the run starts from the simulation's own page.
  it("says Create Simulation, only creates, and names the run button for a voice agent", async () => {
    mocks.agentDefinitions = mocks.voiceAgent;
    const { unmount } = renderPage();

    const create = await walkToSummary();
    expect(
      screen.queryByRole("button", { name: /^run simulation$/i }),
    ).not.toBeInTheDocument();
    fireEvent.click(create);

    await waitFor(() =>
      expect(window.location.pathname).toBe(
        "/dashboard/simulate/test/sim-1/runs",
      ),
    );
    expect(mocks.snackbar).toHaveBeenCalledWith(
      'Simulation created. Click "Run New Simulation" to start a run.',
      { variant: "success" },
    );
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post).toHaveBeenCalledWith(
      "/simulate/run-tests/create/",
      expect.objectContaining({
        name: "Run A",
        agent_definition_id: "agent-1",
        scenario_ids: ["scenario-1"],
      }),
    );
    expect(
      mocks.post.mock.calls.some(([url]) => String(url).includes("/execute/")),
    ).toBe(false);

    // Follow the navigation: a new voice simulation opens on the header that
    // holds "Run New Simulation", not on the SDK steps.
    unmount();
    renderRunsPageAfterCreate("voice");

    expect(await screen.findByTestId("test-run-header")).toBeInTheDocument();
    expect(
      screen.queryByText("Step 1: Install the SDK"),
    ).not.toBeInTheDocument();
  });

  // Before (round 1): the chat message said 'Click "Run from your code"', but a
  // new chat simulation has no executions, so its runs page shows the SDK
  // steps in place of the header and that button is not on the page.
  it("points a chat agent to the SDK steps its new simulation opens on", async () => {
    mocks.agentDefinitions = mocks.chatAgent;
    const { unmount } = renderPage();

    fireEvent.click(await walkToSummary());

    await waitFor(() =>
      expect(window.location.pathname).toBe(
        "/dashboard/simulate/test/sim-1/runs",
      ),
    );
    expect(mocks.snackbar).toHaveBeenCalledWith(
      "Simulation created. Follow the SDK steps on this page to start a run from your code.",
      { variant: "success" },
    );
    expect(mocks.post).toHaveBeenCalledTimes(1);

    // Follow the navigation: the SDK steps the message names are on the page,
    // and the header with the run button is not.
    unmount();
    renderRunsPageAfterCreate("text");

    expect(
      await screen.findByText("Step 1: Install the SDK"),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("Step 3: Create a simulation run"),
    ).toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledWith(
      "/simulate/run-tests/sim-1/sdk-code/",
    );
    expect(screen.queryByTestId("test-run-header")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /run from your code/i }),
    ).not.toBeInTheDocument();
  });
});
