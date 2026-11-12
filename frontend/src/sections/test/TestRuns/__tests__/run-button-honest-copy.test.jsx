/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import TestRunHeader from "../TestRunHeader";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  snackbar: vi.fn(),
  refresh: vi.fn(),
  setSdkCodeOpen: vi.fn(),
  testData: {},
  selectedScenarios: ["scenario-1"],
}));
vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    runTests: { runTest: (id) => `/simulate/run-tests/${id}/execute/` },
    promptSimulation: {
      execute: (prompt, id) =>
        `/simulate/prompt-templates/${prompt}/simulations/${id}/execute/`,
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.snackbar }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: mocks.snackbar }));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ testId: "test-1" }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("../../context/TestDetailContext", () => ({
  useTestDetailContext: () => ({
    testData: mocks.testData,
    refreshTestRunGrid: mocks.refresh,
  }),
}));
vi.mock("../states", () => ({
  useSelectedScenariosStore: () => ({
    selectedScenarios: mocks.selectedScenarios,
    setSelectedScenarios: vi.fn(),
  }),
  useTestRunsSearchStore: () => ({ search: "", setSearch: vi.fn() }),
}));
vi.mock("../state", () => ({
  useTestRunSdkStoreShallow: (selector) =>
    selector({ setSdkCodeOpen: mocks.setSdkCodeOpen }),
}));
vi.mock("../../common", () => ({ useTestRunsSelectedCount: () => 0 }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("../ScenarioPopover", () => ({ default: () => null }));
vi.mock("../TestRunsSelection", () => ({ default: () => null }));
vi.mock("../NewVoiceSimulationDrawer", () => ({ default: () => null }));
// The real tooltip opens only on hover intent; print its text when shown.
vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ show, title, children }) => (
    <>
      {show ? <div data-testid="run-tooltip">{title}</div> : null}
      {children}
    </>
  ),
}));

const clients = [];
const renderHeader = () => {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <TestRunHeader />
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.selectedScenarios = ["scenario-1"];
  mocks.post.mockResolvedValue({ data: { status: "started" } });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("TestRunHeader run button", () => {
  // Before: a chat agent's button read "Run New Simulation" but opened the
  // SDK steps; the run itself starts from the user's code.
  it("says 'Run from your code' for a chat agent and opens the SDK steps", () => {
    mocks.testData = {
      source_type: "agent_definition",
      agent_definition: "agent-1",
      agent_definition_detail: { agent_type: "text" },
      scenarios: ["scenario-1"],
    };
    renderHeader();

    expect(
      screen.queryByRole("button", { name: "Run New Simulation" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run from your code" }));

    expect(mocks.setSdkCodeOpen).toHaveBeenCalledWith(true);
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("keeps 'Run New Simulation' for a voice agent, which runs in the browser", async () => {
    mocks.testData = {
      source_type: "agent_definition",
      agent_definition: "agent-1",
      agent_definition_detail: { agent_type: "voice" },
      scenarios: ["scenario-1"],
    };
    renderHeader();

    expect(
      screen.queryByRole("button", { name: "Run from your code" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run New Simulation" }));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/simulate/run-tests/test-1/execute/",
        { select_all: false, scenario_ids: ["scenario-1"] },
      ),
    );
    expect(mocks.setSdkCodeOpen).not.toHaveBeenCalled();
  });

  it("keeps 'Run New Simulation' for a prompt simulation, which runs in the browser", async () => {
    mocks.testData = {
      source_type: "prompt",
      prompt_template: "prompt-1",
      scenarios: ["scenario-1"],
    };
    renderHeader();

    fireEvent.click(screen.getByRole("button", { name: "Run New Simulation" }));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/simulate/prompt-templates/prompt-1/simulations/test-1/execute/",
        {},
      ),
    );
    expect(mocks.setSdkCodeOpen).not.toHaveBeenCalled();
  });

  // Before: "Select atleast one scenario to run test".
  it("explains a disabled button in plain words when no scenario is selected", () => {
    mocks.selectedScenarios = [];
    mocks.testData = {
      source_type: "agent_definition",
      agent_definition: "agent-1",
      agent_definition_detail: { agent_type: "voice" },
      scenarios: [],
    };
    renderHeader();

    expect(screen.getByTestId("run-tooltip")).toHaveTextContent(
      "Select at least one scenario to run the simulation.",
    );
    expect(screen.queryByText(/atleast/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Run New Simulation" }),
    ).toBeDisabled();
  });
});
