/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import TestRunDetailHeader from "../TestRunDetailHeader";

// A run started on saved dataset rows skips the rows that no longer exist.
// The run's page says how many, from the count the run stored.
const ONE_SKIPPED = "1 saved row no longer exists and was skipped.";
const TWO_SKIPPED = "2 saved rows no longer exist and were skipped.";
const ANY_SKIPPED = /saved rows? no longer exists? and (was|were) skipped/;

const mocks = vi.hoisted(() => ({ kpis: null }));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ executionId: "exec-1", testId: "test-1" }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    runTests: {
      executionDetailsExport: (id) => `/simulate/test-executions/${id}/export/`,
    },
  },
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("../context/TestDetailContext", () => ({
  useTestDetail: () => ({ refreshGrid: vi.fn() }),
}));
vi.mock("src/sections/test/common", () => ({
  useCancelExecution: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../states", () => ({
  useTestExecutionStore: (selector) => selector({ status: "completed" }),
}));
vi.mock("../common", () => ({
  RerunTestOptions: { EVAL_ONLY: "eval_only", CALL_AND_EVAL: "call_and_eval" },
  TestRunLoadingStatus: ["pending", "running", "evaluating", "cancelling"],
}));
vi.mock("src/api/tests/testDetails", () => ({
  useRerunTest: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/hooks/useKpis", () => ({
  default: () => ({ data: mocks.kpis }),
}));
vi.mock("src/hooks/useTestRunDetails", () => ({
  default: () => ({
    data: { source_type: "prompt", prompt_template: "prompt-1" },
  }),
}));
vi.mock("src/sections/test/TestRuns/state", () => ({
  useTestRunSdkStoreShallow: (selector) =>
    selector({ setSdkCodeOpen: vi.fn() }),
}));
vi.mock("src/sections/test/TestRuns/NewVoiceSimulationDrawer", () => ({
  default: () => null,
}));
vi.mock("../TestDetailBreadcrumb", () => ({ default: () => null }));
vi.mock("src/sections/test/CallLogs/CallStatus", () => ({
  default: () => null,
}));
vi.mock("../components/CallMetadataSection", () => ({
  default: () => null,
}));
vi.mock("src/components/CallLogsDrawer/CustomCallLogHeader", () => ({
  formatDurationSafe: () => "0s",
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/custom-dialog", () => ({ ConfirmDialog: () => null }));

const clients = [];
const renderRunPage = (kpis) => {
  mocks.kpis = { total_calls: 2, agent_type: "text", ...kpis };
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <TestRunDetailHeader />
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.kpis = null;
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("Skipped saved rows on a run's page", () => {
  it.each([
    ["no row was skipped", { skipped_dataset_row_count: 0 }],
    ["the run stored no count", {}],
  ])("shows no line when %s", (_case, kpis) => {
    renderRunPage(kpis);

    expect(screen.getByText("Execution : exec-1")).toBeInTheDocument();
    expect(screen.queryByText(ANY_SKIPPED)).not.toBeInTheDocument();
  });

  it("says one saved row was skipped, in the singular", () => {
    renderRunPage({ skipped_dataset_row_count: 1 });

    expect(screen.queryByText(ONE_SKIPPED)).toBeInTheDocument();
  });

  it("says how many saved rows were skipped, in the plural", () => {
    renderRunPage({ skipped_dataset_row_count: 2 });

    expect(screen.queryByText(TWO_SKIPPED)).toBeInTheDocument();
  });
});
