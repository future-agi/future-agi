/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  renderWithRouter,
  screen,
  waitFor,
  fireEvent,
  userEvent,
} from "src/utils/test-utils";

import CreateRunTestPage from "../CreateRunTestPage";

// Hook return values must stay referentially stable across renders (see
// scenario-step-loading.test.jsx). Each agent also needs its own versions
// object: picking an agent clears the version, and only a new
// `versionOptions` identity re-runs the effect that picks the first one.
const mocks = vi.hoisted(() => {
  const versions = (id) => ({
    data: { pages: [{ results: [{ id, version_name_display: "v1" }] }] },
    isLoading: false,
    fetchNextPage: () => {},
    isFetchingNextPage: false,
  });
  return {
    get: vi.fn(),
    post: vi.fn(),
    snackbar: vi.fn(),
    agentDefinitions: {
      // AGENT_TYPES.CHAT is "text".
      agentDefinitions: [
        { id: "agent-voice", agent_name: "Voice Agent", agent_type: "voice" },
        { id: "agent-chat", agent_name: "Chat Agent", agent_type: "text" },
      ],
      fetchNextPage: () => {},
      isLoading: false,
      isFetchingNextPage: false,
    },
    versionsByAgent: {
      "agent-voice": versions("version-voice"),
      "agent-chat": versions("version-chat"),
    },
    noVersions: {
      data: { pages: [] },
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
    mocks.versionsByAgent[selectedAgentId] ?? mocks.noVersions,
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

const row = (id, name, datasetRows = 5) => ({
  id,
  name,
  description: "desc",
  dataset_rows: datasetRows,
});

// Two pages of ten, as /simulate/scenarios/ pages them.
const pagedList = ({ page = 1 }) => ({
  count: 20,
  results: Array.from({ length: 10 }, (_, i) =>
    row(`p${page}-s${i}`, `Scenario ${page}-${i}`),
  ),
});

// What /simulate/scenarios/ returns for the request's params. The live list
// is filtered by agent_type (simulate/views/scenarios.py), so each test says
// what exists for which type.
let scenarioList;

beforeEach(() => {
  vi.clearAllMocks();
  window.history.pushState({}, "", "/");
  vi.spyOn(window, "open").mockImplementation(() => null);
  scenarioList = () => ({ count: 0, results: [] });
  mocks.get.mockImplementation((url, config) => {
    if (url === "/scenarios/")
      return Promise.resolve({ data: scenarioList(config?.params ?? {}) });
    return Promise.resolve({ data: {} });
  });
  mocks.post.mockResolvedValue({ data: { id: "sim-1" } });
});

afterEach(() => {
  window.open.mockRestore?.();
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

const nextButton = () => screen.getByRole("button", { name: /^next$/i });
const backButton = () => screen.getByRole("button", { name: /^back$/i });

const pickAgent = async (agentName) => {
  fireEvent.click(
    screen.getByPlaceholderText(/Choose your agent that you want to test/i),
  );
  fireEvent.click(await screen.findByText(agentName));
  // An effect auto-selects the agent's first version; Next waits for it.
  await waitFor(() => expect(nextButton()).toBeEnabled());
};

// Step 1 (name + agent), then on to Choose Scenario(s).
const enterDetails = async (agentName) => {
  fireEvent.change(screen.getByPlaceholderText(/Enter a name for your/i), {
    target: { value: "Run A" },
  });
  await pickAgent(agentName);
  fireEvent.click(nextButton());
};

// From a step with a scenario ticked: add one evaluation and open Summary.
const addEvaluationAndOpenSummary = async () => {
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

describe("Choose Scenario(s): none for this agent type", () => {
  // Before: "Add your first scenario" although voice scenarios existed, and
  // Add Scenario left the wizard (router.push), losing what was entered.
  it("names the agent type and opens scenario creation in a new tab", async () => {
    scenarioList = ({ agent_type }) =>
      agent_type === "voice"
        ? { count: 1, results: [row("scn-a", "Voice scenario A")] }
        : { count: 0, results: [] };

    renderPage();
    fireEvent.change(
      screen.getByPlaceholderText(/Describe what this simulation run/i),
      { target: { value: "Checks refunds" } },
    );
    await enterDetails("Chat Agent");

    expect(
      await screen.findByText("No scenarios for chat agents yet"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/only scenarios for chat agents can run/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Add your first scenario"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /add scenario/i }));

    expect(window.open).toHaveBeenCalledWith(
      "/dashboard/simulate/scenarios/create",
      "_blank",
    );
    expect(window.location.pathname).toBe("/");

    // The wizard is still here with what was entered.
    fireEvent.click(backButton());
    expect(await screen.findByDisplayValue("Run A")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Checks refunds")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Chat Agent")).toBeInTheDocument();
  });
});

describe("Choose Scenario(s): what is ticked is what is saved", () => {
  // Before: the voice scenario stayed selected after switching to a chat
  // agent, hidden from the list, counted in Summary and saved.
  it("clears scenarios ticked for another agent type when the agent changes", async () => {
    scenarioList = ({ agent_type }) =>
      agent_type === "voice"
        ? { count: 1, results: [row("scn-a", "Voice scenario A")] }
        : { count: 1, results: [row("scn-c", "Chat scenario C")] };

    renderPage();
    await enterDetails("Voice Agent");
    fireEvent.click(await screen.findByText("Voice scenario A"));
    await waitFor(() => expect(nextButton()).toBeEnabled());

    fireEvent.click(backButton());
    await pickAgent("Chat Agent");
    fireEvent.click(nextButton());

    expect(await screen.findByText("Chat scenario C")).toBeInTheDocument();
    expect(screen.queryByText("Voice scenario A")).not.toBeInTheDocument();
    // Nothing on the list is ticked, so there is nothing to go on with.
    expect(nextButton()).toBeDisabled();

    fireEvent.click(screen.getByText("Chat scenario C"));
    const create = await addEvaluationAndOpenSummary();

    expect(screen.getByText("1 scenario(s) selected")).toBeInTheDocument();
    expect(screen.getByText("Chat scenario C")).toBeInTheDocument();

    fireEvent.click(create);
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect(mocks.post).toHaveBeenCalledWith(
      "/simulate/run-tests/create/",
      expect.objectContaining({
        agent_definition_id: "agent-chat",
        scenario_ids: ["scn-c"],
      }),
    );
  });

  // Before: Summary looked ticked scenarios up on the list's current page only.
  it("lists a scenario ticked on another page in the Summary", async () => {
    scenarioList = pagedList;

    renderPage();
    await enterDetails("Chat Agent");
    fireEvent.click(await screen.findByText("Scenario 1-0"));
    fireEvent.click(screen.getByRole("button", { name: /next page/i }));
    expect(await screen.findByText("Scenario 2-0")).toBeInTheDocument();

    await addEvaluationAndOpenSummary();

    expect(screen.getByText("1 scenario(s) selected")).toBeInTheDocument();
    expect(screen.getByText("Scenario 1-0")).toBeInTheDocument();
  });

  // Before: typing a search on page 2 asked for page 2 of the search results.
  it("starts a new search at its first page", async () => {
    scenarioList = pagedList;

    renderPage();
    await enterDetails("Chat Agent");
    await screen.findByText("Scenario 1-0");
    fireEvent.click(screen.getByRole("button", { name: /next page/i }));
    await screen.findByText("Scenario 2-0");

    fireEvent.change(screen.getByPlaceholderText("Search scenarios..."), {
      target: { value: "refund" },
    });

    await waitFor(
      () =>
        expect(mocks.get).toHaveBeenCalledWith("/scenarios/", {
          params: expect.objectContaining({ search: "refund", page: 1 }),
        }),
      { timeout: 2000 },
    );
    expect(mocks.get).not.toHaveBeenCalledWith("/scenarios/", {
      params: expect.objectContaining({ search: "refund", page: 2 }),
    });
  });
});

describe("Choose Scenario(s): steps done for another agent type", () => {
  // Before: switching to an agent of another type on step 1 cleared the
  // scenarios but left Choose Scenario(s) and Select Evaluations checked, so
  // a click on the "Select Evaluations" step skipped the now-empty scenario
  // step and Create sent no scenarios.
  it("keeps the later steps closed after the agent type changes", async () => {
    scenarioList = ({ agent_type }) =>
      agent_type === "voice"
        ? { count: 1, results: [row("scn-a", "Voice scenario A")] }
        : { count: 1, results: [row("scn-c", "Chat scenario C")] };

    renderPage();
    await enterDetails("Voice Agent");
    fireEvent.click(await screen.findByText("Voice scenario A"));
    await addEvaluationAndOpenSummary();

    // Summary -> Select Evaluations -> Choose Scenario(s) -> step 1.
    fireEvent.click(backButton());
    fireEvent.click(backButton());
    fireEvent.click(backButton());
    await pickAgent("Chat Agent");

    // The step label, not the "Select evaluations" heading of that step.
    fireEvent.click(screen.getByText("Select Evaluations"));
    expect(
      screen.getByPlaceholderText(/Enter a name for your/i),
    ).toBeInTheDocument();
    expect(screen.queryByText("Select evaluations")).not.toBeInTheDocument();

    fireEvent.click(nextButton());
    expect(await screen.findByText("Chat scenario C")).toBeInTheDocument();
    expect(nextButton()).toBeDisabled();
    fireEvent.click(screen.getByText("Select Evaluations"));
    expect(
      screen.getByPlaceholderText("Search scenarios..."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Select evaluations")).not.toBeInTheDocument();
  });

  // Before: unticking every scenario, going Back to step 1 and clicking the
  // done "Select Evaluations" step still reached Summary, and Create sent
  // scenario_ids: [], which the server refuses with only "This list may not
  // be empty."
  it("opens Choose Scenario(s) instead of creating a simulation with none", async () => {
    scenarioList = () => ({
      count: 1,
      results: [row("scn-c", "Chat scenario C")],
    });

    renderPage();
    await enterDetails("Chat Agent");
    fireEvent.click(await screen.findByText("Chat scenario C"));
    await addEvaluationAndOpenSummary();

    // Back to the scenarios, untick the only one, then back to step 1.
    fireEvent.click(backButton());
    fireEvent.click(backButton());
    fireEvent.click(await screen.findByText("Chat scenario C"));
    await waitFor(() => expect(nextButton()).toBeDisabled());
    fireEvent.click(backButton());

    fireEvent.click(screen.getByText("Select Evaluations"));
    await screen.findByText("Select evaluations");
    fireEvent.click(nextButton());
    const create = await screen.findByRole("button", {
      name: /^create simulation$/i,
    });
    expect(screen.getByText("0 scenario(s) selected")).toBeInTheDocument();

    fireEvent.click(create);

    expect(mocks.snackbar).toHaveBeenCalledWith("Pick at least one scenario", {
      variant: "error",
    });
    expect(mocks.post).not.toHaveBeenCalled();
    expect(
      await screen.findByPlaceholderText("Search scenarios..."),
    ).toBeInTheDocument();
  });
});

describe("Choose Scenario(s): keyboard", () => {
  // Before: the rows had only a click handler and each checkbox had
  // tabIndex -1, so Tab skipped every scenario and Next stayed disabled.
  it("reaches a scenario's checkbox with Tab and ticks it with Space", async () => {
    scenarioList = () => ({
      count: 2,
      results: [
        row("scn-1", "Refund request"),
        row("scn-2", "Empty scenario", 0),
      ],
    });
    const user = userEvent.setup();

    renderPage();
    await enterDetails("Chat Agent");
    await screen.findByText("Refund request");
    expect(nextButton()).toBeDisabled();

    await user.click(screen.getByPlaceholderText("Search scenarios..."));
    await user.tab();

    // Focus lands on the first scenario's checkbox, named by the scenario.
    expect(document.activeElement).toHaveAttribute("type", "checkbox");
    const refund = screen.getByRole("checkbox", { name: "Refund request" });
    expect(refund).toHaveFocus();

    await user.keyboard(" ");
    expect(refund).toBeChecked();
    expect(nextButton()).toBeEnabled();

    // A click on the box itself toggles once, not twice (box, then row).
    fireEvent.click(refund);
    expect(refund).not.toBeChecked();

    // A scenario with no datapoints still cannot be ticked.
    expect(
      screen.getByRole("checkbox", { name: "Empty scenario" }),
    ).toBeDisabled();
  });
});
