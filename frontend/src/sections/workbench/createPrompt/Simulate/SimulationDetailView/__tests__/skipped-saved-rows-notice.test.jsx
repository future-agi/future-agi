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
import EmptyExecutionsState from "../EmptyExecutionsState";
import SimulationDetailHeader from "../SimulationDetailHeader";

// A prompt simulation started on saved dataset rows skips the rows that no
// longer exist and says how many, in a calm notice after it has started.
const STARTED = ["Simulation started successfully", { variant: "success" }];
const ONE_SKIPPED = [
  "1 saved row no longer exists and was skipped.",
  { variant: "info" },
];
const FOUR_SKIPPED = [
  "4 saved rows no longer exist and were skipped.",
  { variant: "info" },
];

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  snackbar: vi.fn(),
  refreshGrid: vi.fn(),
  refetchExecutions: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post, patch: vi.fn() },
  endpoints: {
    runTests: {
      deleteSimulation: (id) =>
        `/simulate/run-tests/${id}/delete-test-executions/`,
    },
    promptSimulation: {
      execute: (prompt, id) =>
        `/simulate/prompt-templates/${prompt}/simulations/${id}/execute/`,
      detail: (prompt, id) =>
        `/simulate/prompt-templates/${prompt}/simulations/${id}/`,
    },
  },
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: mocks.snackbar }));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ id: "prompt-1" }),
}));
vi.mock("../context/SimulationDetailContext", () => ({
  useSimulationDetailContext: () => ({
    simulation: {
      id: "simulation-1",
      name: "Saved rows simulation",
      prompt_version: "version-1",
      scenarios: ["scenario-1"],
      scenarios_detail: [{ id: "scenario-1", dataset_rows: 3 }],
    },
    isLoading: false,
    executionsCount: 1,
    refreshGrid: mocks.refreshGrid,
    refetchExecutions: mocks.refetchExecutions,
    searchQuery: "",
    setSearchQuery: vi.fn(),
    getGridApi: () => null,
  }),
}));
vi.mock("../states", () => ({
  useSimulationEvaluationStoreShallow: (selector) =>
    selector({ setOpenEvaluation: vi.fn(), openEvaluation: null }),
  useSimulationExecutionsGridStoreShallow: (selector) =>
    selector({
      toggledNodes: [],
      selectAll: false,
      totalRowCount: 0,
      setSelectAll: vi.fn(),
      setToggledNodes: vi.fn(),
    }),
}));
vi.mock("../common", () => ({
  DRAWER_OPEN_ENUMS: { EVALS: "evals", DELETE: "delete" },
  getSelectedCount: () => 0,
}));
vi.mock("src/sections/test/TestRuns/states", () => ({
  useSelectedScenariosStore: () => ({
    selectedScenarios: ["scenario-1"],
    setSelectedScenarios: vi.fn(),
  }),
}));
vi.mock("../VersionSelect", () => ({ default: () => null }));
vi.mock("../SimulationEvaluationDrawer", () => ({ default: () => null }));
vi.mock("../SimulationExecutionsSelection", () => ({ default: () => null }));
vi.mock("src/sections/test/TestRuns/ScenarioPopover", () => ({
  default: () => null,
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => <>{children}</>,
}));
vi.mock("src/components/ModalWrapper/ModalWrapper", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));

// The prompt simulation endpoint wraps its answer in { status, result }.
const answer = (skipped) => ({
  data: {
    status: true,
    result: {
      status: "started",
      total_calls: 2,
      ...(skipped === undefined ? {} : { skipped_dataset_row_count: skipped }),
    },
  },
});

const clients = [];
const renderWithClient = (ui) => {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
};

const runFrom = {
  "the empty runs list": async () => {
    renderWithClient(<EmptyExecutionsState />);
    fireEvent.click(screen.getByRole("button", { name: "Run Simulation Now" }));
    await waitFor(() => expect(mocks.refetchExecutions).toHaveBeenCalled());
  },
  "the simulation header": async () => {
    renderWithClient(<SimulationDetailHeader onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Run Simulation" }));
    await waitFor(() => expect(mocks.refreshGrid).toHaveBeenCalled());
  },
};
const PLACES = Object.keys(runFrom);

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe.each(PLACES)("Running a prompt simulation from %s", (from) => {
  it.each([
    ["no row was skipped", 0],
    ["the answer has no count", undefined],
  ])("shows no skipped-rows notice when %s", async (_case, skipped) => {
    mocks.post.mockResolvedValue(answer(skipped));

    await runFrom[from]();

    expect(mocks.post).toHaveBeenCalledWith(
      "/simulate/prompt-templates/prompt-1/simulations/simulation-1/execute/",
      {},
    );
    expect(mocks.snackbar.mock.calls).toEqual([STARTED]);
  });

  it("says one saved row was skipped, in the singular", async () => {
    mocks.post.mockResolvedValue(answer(1));

    await runFrom[from]();

    expect(mocks.snackbar.mock.calls).toEqual([STARTED, ONE_SKIPPED]);
  });

  it("says how many saved rows were skipped, in the plural", async () => {
    mocks.post.mockResolvedValue(answer(4));

    await runFrom[from]();

    expect(mocks.snackbar.mock.calls).toEqual([STARTED, FOUR_SKIPPED]);
  });
});
