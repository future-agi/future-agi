/* eslint-disable react/prop-types */
// The row drawer's Expected section for a row whose Expected cell is empty
// in an experiment that has an Expected answers column. The results read
// sends `{value: null, primary: <column>}` for that row: the drawer says
// "No expected value", not "This dataset has no reference column".
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";

const requests = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: requests,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), Events: {} }));
// Like the real viewer, an empty value shows its `emptyLabel`.
vi.mock("src/sections/common/ValueViewer/ValueViewer", () => ({
  default: ({ value, emptyLabel = "empty" }) => (
    <div data-testid="value">
      {value == null || value === ""
        ? emptyLabel
        : typeof value === "string"
          ? value
          : JSON.stringify(value)}
    </div>
  ),
}));
// Only the Input and Expected sections render for real; the rest of the tab
// is stubbed (each stub keeps the module's other exports).
vi.mock(
  "src/sections/experiment-detail/results/drawer/OutputCard",
  async (original) => ({ ...(await original()), default: () => null }),
);
vi.mock(
  "src/sections/experiment-detail/results/drawer/ProductionRetestCard",
  async (original) => ({ ...(await original()), default: () => null }),
);
vi.mock("src/sections/common/BestOf/BestOfCard", async (original) => ({
  ...(await original()),
  default: () => null,
}));
vi.mock(
  "src/sections/annotations/queues/components/add-to-queue-dialog",
  async (original) => ({ ...(await original()), default: () => null }),
);
vi.mock("src/sections/common/Lenses/ModalityLens", async (original) => ({
  ...(await original()),
  default: () => null,
}));
vi.mock(
  "src/sections/experiment-detail/results/drawer/ScoreCard",
  async (original) => ({ ...(await original()), default: () => null }),
);
vi.mock(
  "src/sections/experiment-detail/results/AddResultsToDatasetDialog",
  async (original) => ({ ...(await original()), default: () => null }),
);
vi.mock(
  "src/sections/develop-detail/Review/CorrectedOutputsBlock",
  async (original) => ({ ...(await original()), default: () => null }),
);
vi.mock(
  "src/sections/develop-detail/Review/PairVoteStrip",
  async (original) => ({ ...(await original()), default: () => null }),
);
vi.mock("src/api/develop/run-results", async (original) => ({
  ...(await original()),
  useRerunResultCells: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock(
  "src/sections/experiment-detail/results/drawer/ScoreMatrix",
  async (original) => ({
    ...(await original()),
    default: () => null,
    hasTrialGlyphs: () => false,
  }),
);
vi.mock(
  "src/sections/experiment-detail/results/drawer/usePairVote",
  async (original) => ({ ...(await original()), usePairVote: () => ({}) }),
);

import { datasetColumnsKey } from "src/sections/develop/useDatasetColumnsLite";
import ResultCompareTab from "../ResultCompareTab";

const DATASET = "8b1c0f6e-0000-4000-8000-00000000d5e8";
const RUN = { run_id: "r1", label: "#1A", name: "Prompt run" };
const NO_REFERENCE = "This dataset has no reference column";
const clients = [];

const row = (expected) => ({
  example_id: "e1",
  inputs: { input: "Japan" },
  expected,
  results: {
    r1: {
      status: "completed",
      output: "Kyoto",
      trials_detail: [{ output: "Kyoto" }],
    },
  },
});

function openDrawer(expected) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  client.setQueryData(datasetColumnsKey(DATASET), {
    data: { result: { columns: [] } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <ResultCompareTab
        datasetId={DATASET}
        row={row(expected)}
        runs={[RUN]}
        baselineId="r1"
        focusedRunId="r1"
        trial={1}
        summary={{ metrics: [] }}
        colors={{}}
        diff="off"
        onDiffChange={vi.fn()}
        onTrialChange={vi.fn()}
        onOpenTab={vi.fn()}
        readOnly
        rowIds={["e1"]}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  requests.get.mockResolvedValue({ data: { status: true, result: {} } });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("row drawer Expected for an empty Expected cell", () => {
  it("says No expected value when the experiment has the column", async () => {
    openDrawer({ value: null, primary: "expected_output" });
    expect(
      (await screen.findAllByText("No expected value")).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText(NO_REFERENCE)).toBeNull();
  });

  it("shows the value of a filled Expected cell", async () => {
    openDrawer({
      value: { expected_output: "Tokyo, Japan" },
      primary: "expected_output",
    });
    expect((await screen.findAllByText("Tokyo, Japan")).length).toBeGreaterThan(
      0,
    );
    expect(screen.queryByText(NO_REFERENCE)).toBeNull();
    expect(screen.queryByText("No expected value")).toBeNull();
  });

  it("keeps its no reference column line when the experiment has none", async () => {
    openDrawer({ value: null, reason: "no_reference_column" });
    expect(await screen.findByText(NO_REFERENCE)).toBeInTheDocument();
  });
});
