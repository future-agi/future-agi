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

// A run started on saved dataset rows skips the rows that no longer exist and
// says how many, in a calm notice after the run has started.
const STARTED = ["Test run started", { variant: "success" }];
const ONE_SKIPPED = [
  "1 saved row no longer exists and was skipped.",
  { variant: "info" },
];
const THREE_SKIPPED = [
  "3 saved rows no longer exist and were skipped.",
  { variant: "info" },
];

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  snackbar: vi.fn(),
  refresh: vi.fn(),
  testData: {},
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
    selectedScenarios: ["scenario-1"],
    setSelectedScenarios: vi.fn(),
  }),
  useTestRunsSearchStore: () => ({ search: "", setSearch: vi.fn() }),
}));
vi.mock("../state", () => ({
  useTestRunSdkStoreShallow: (selector) =>
    selector({ setSdkCodeOpen: vi.fn() }),
}));
vi.mock("../../common", () => ({ useTestRunsSelectedCount: () => 0 }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("../ScenarioPopover", () => ({ default: () => null }));
vi.mock("../TestRunsSelection", () => ({ default: () => null }));
vi.mock("../NewVoiceSimulationDrawer", () => ({ default: () => null }));

const VOICE_AGENT = {
  source_type: "agent_definition",
  agent_definition: "agent-1",
  agent_definition_detail: { agent_type: "voice" },
  scenarios: ["scenario-1"],
};
const PROMPT = {
  source_type: "prompt",
  prompt_template: "prompt-1",
  scenarios: ["scenario-1"],
};

// The run test endpoint answers with a bare body; the prompt simulation
// endpoint wraps it in { status, result }.
const answer = (testData, skipped) => {
  const body = { status: "started", total_calls: 2 };
  if (skipped !== undefined) body.skipped_dataset_row_count = skipped;
  return testData === PROMPT
    ? { data: { status: true, result: body } }
    : { data: body };
};

const clients = [];
async function startRun() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <TestRunHeader />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Run New Simulation" }));
  await waitFor(() => expect(mocks.refresh).toHaveBeenCalledTimes(1));
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe.each([
  ["a voice agent's run", VOICE_AGENT],
  ["a prompt simulation", PROMPT],
])("Run New Simulation on %s", (_name, testData) => {
  beforeEach(() => {
    mocks.testData = testData;
  });

  it.each([
    ["no row was skipped", 0],
    ["the answer has no count", undefined],
  ])("shows no skipped-rows notice when %s", async (_case, skipped) => {
    mocks.post.mockResolvedValue(answer(testData, skipped));

    await startRun();

    expect(mocks.snackbar.mock.calls).toEqual([STARTED]);
  });

  it("says one saved row was skipped, in the singular", async () => {
    mocks.post.mockResolvedValue(answer(testData, 1));

    await startRun();

    expect(mocks.snackbar.mock.calls).toEqual([STARTED, ONE_SKIPPED]);
  });

  it("says how many saved rows were skipped, in the plural", async () => {
    mocks.post.mockResolvedValue(answer(testData, 3));

    await startRun();

    expect(mocks.snackbar.mock.calls).toEqual([STARTED, THREE_SKIPPED]);
  });
});
