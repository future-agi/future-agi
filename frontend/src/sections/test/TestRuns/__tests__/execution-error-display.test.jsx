import React from "react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import { handleError } from "src/utils/queryErrorHandler";
import TestRunHeader from "../TestRunHeader";

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

// Independent pins from CBSEC delivery-001, not the implementation allowlist.
const S2 =
  "Failed to start test execution. Check the test runs before trying again.";
const S3 = "Test execution service is unavailable. Please try again later.";
const S4 = "Failed to execute test. Check the test runs before trying again.";
const FALLBACK = "Something went wrong";
const VALIDATION = "At least one scenario is required to execute the test.";
const cases = [
  ["S2-500", 500, S2, S2],
  ["S3-503", 503, S3, S3],
  ["S3-500", 500, S3, S3],
  ["S4-500", 500, S4, S4],
  ["private-500", 500, "Traceback: execution_id=PRIVATE_ID", FALLBACK],
  ["suffix-500", 500, `${S2} execution_id=PRIVATE_ID`, FALLBACK],
  ["whitespace-500", 500, ` ${S3}`, FALLBACK],
  ["validation-400", 400, VALIDATION, VALIDATION],
];
const clients = [];

function runSimulation() {
  const globalError = vi.fn(handleError);
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: globalError }),
    defaultOptions: { mutations: { retry: false } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <TestRunHeader />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Run New Simulation" }));
  return globalError;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.testData = {
    source_type: "voice",
    agent_definition: "agent-1",
    agent_definition_detail: { agent_type: "voice" },
    scenarios: ["scenario-1"],
  };
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe.each(["flattened", "nested"])(
  "TestRunHeader execution copy (%s)",
  (shape) => {
    it.each(cases)(
      "%s shows exactly one safe snackbar",
      async (_id, status, message, expected) => {
        const body = { code: "server_error", message, result: message };
        mocks.post.mockRejectedValue(
          shape === "flattened"
            ? { ...body, statusCode: status }
            : { response: { status, data: body } },
        );
        const globalError = runSimulation();
        await waitFor(() => {
          expect(mocks.snackbar.mock.calls).toEqual([
            [expected, { variant: "error" }],
          ]);
        });
        expect(globalError).toHaveBeenCalledTimes(1);
        expect(mocks.post).toHaveBeenCalledExactlyOnceWith(
          "/simulate/run-tests/test-1/execute/",
          { select_all: false, scenario_ids: ["scenario-1"] },
        );
        expect(mocks.refresh).not.toHaveBeenCalled();
      },
    );
  },
);

it("keeps the prompt execution endpoint outside the approved-copy exception", async () => {
  mocks.testData = {
    ...mocks.testData,
    source_type: "prompt",
    prompt_template: "prompt-1",
  };
  mocks.post.mockRejectedValue({ statusCode: 500, result: S2 });
  runSimulation();
  await waitFor(() =>
    expect(mocks.snackbar.mock.calls).toEqual([
      [FALLBACK, { variant: "error" }],
    ]),
  );
  expect(mocks.post).toHaveBeenCalledExactlyOnceWith(
    "/simulate/prompt-templates/prompt-1/simulations/test-1/execute/",
    {},
  );
});

it("preserves structured 400 validation from the existing global handler", async () => {
  mocks.post.mockRejectedValue({
    statusCode: 400,
    result: { details: { scenario_ids: [VALIDATION] } },
  });
  runSimulation();
  await waitFor(() =>
    expect(mocks.snackbar.mock.calls).toEqual([
      [VALIDATION, { variant: "error" }],
    ]),
  );
});

it("leaves rate-limit notifications to the existing interceptor", async () => {
  mocks.post.mockRejectedValue({
    statusCode: 429,
    result: "Rate limit reached.",
  });
  runSimulation();
  await waitFor(() =>
    expect(clients.at(-1).getMutationCache().getAll()[0]?.state.status).toBe(
      "error",
    ),
  );
  expect(mocks.snackbar).not.toHaveBeenCalled();
});

it("preserves successful execution and refresh", async () => {
  mocks.post.mockResolvedValue({ data: { status: "started" } });
  runSimulation();
  await waitFor(() => expect(mocks.refresh).toHaveBeenCalledTimes(1));
  expect(mocks.snackbar.mock.calls).toEqual([
    ["Test run started", { variant: "success" }],
  ]);
  expect(mocks.post).toHaveBeenCalledTimes(1);
});
