/* eslint-disable react/prop-types */
// The result drawer reads an agent (native) run's input envelope
// {"values": {"<dataset column id>": "..."}} as the grid's Input cell does:
// each value under its column name (the text when there is one), from the
// dataset columns read the grid already cached. Raw JSON values in the grid
// shows what was stored.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";

const requests = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const outputCard = vi.hoisted(() => vi.fn());
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: requests,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), Events: {} }));
vi.mock("src/sections/common/ValueViewer/ValueViewer", () => ({
  default: ({ value }) => (
    <div data-testid="value">
      {typeof value === "string" ? value : JSON.stringify(value)}
    </div>
  ),
}));
// Only the Input section renders for real; the rest of the tab is stubbed
// (each stub keeps the module's other exports).
vi.mock(
  "src/sections/experiment-detail/results/drawer/OutputCard",
  async (original) => ({
    ...(await original()),
    default: (props) => {
      outputCard(props);
      return null;
    },
  }),
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

import { endpoints } from "src/utils/axios";
import { runResultsKey } from "src/api/develop/run-results";
import { datasetColumnsKey } from "src/sections/develop/useDatasetColumnsLite";
import ResultCompareTab from "../ResultCompareTab";
import usePreviewField from "../usePreviewField";
import { InputCell } from "../../cells/ExampleCells";
import { columnNamesById } from "../../cells/displayInputs";

const DATASET = "8b1c0f6e-0000-4000-8000-00000000d5e7";
const QUESTION_ID = "884f2c0e-51a0-43b9-9104-5b6d463fc839";
const CONTEXT_ID = "5ef7eb3f-ef12-4336-ab53-b44225022fcd";
const COLUMNS = [
  { id: QUESTION_ID, name: "question", data_type: "text" },
  { id: CONTEXT_ID, name: "context", data_type: "text" },
];
const RUN = { run_id: "r1", label: "#1A", name: "Agent run" };
const clients = [];

const agentRow = (values) => ({
  example_id: "e1",
  inputs: { values },
  results: {
    r1: { status: "completed", output: "4", trials_detail: [{ output: "4" }] },
  },
});

function queryClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  // The grid's dataset columns read (RunResultsView), already in the cache.
  client.setQueryData(datasetColumnsKey(DATASET), {
    data: { result: { columns: COLUMNS } },
  });
  clients.push(client);
  return client;
}

function openDrawer(row) {
  render(
    <QueryClientProvider client={queryClient()}>
      <ResultCompareTab
        datasetId={DATASET}
        row={row}
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

const columnsUrl = () => endpoints.develop.getDatasetColumns(DATASET);

beforeEach(() => {
  vi.clearAllMocks();
  requests.get.mockResolvedValue({ data: { status: true, result: {} } });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("result drawer Input for an agent run row", () => {
  it("shows each value under its column name, from the cached columns", async () => {
    openDrawer(
      agentRow({ [QUESTION_ID]: "What is 2 + 2?", [CONTEXT_ID]: "Arithmetic" }),
    );
    expect(await screen.findByText("question")).toBeInTheDocument();
    expect(screen.getByText("What is 2 + 2?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show 1 more field" }));
    expect(screen.getByText("context")).toBeInTheDocument();
    expect(screen.getByText("Arithmetic")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(QUESTION_ID);
    expect(document.body.textContent).not.toContain(CONTEXT_ID);
    expect(document.body.textContent).not.toContain('"values"');
    // The output cards' prompt / agent prefill read the same names.
    expect(outputCard).toHaveBeenCalled();
    expect(outputCard.mock.calls.at(-1)[0].row.inputs).toEqual({
      question: "What is 2 + 2?",
      context: "Arithmetic",
    });
    // No second dataset columns request: the grid's cached read is used.
    expect(requests.get.mock.calls.map(([url]) => url)).not.toContain(
      columnsUrl(),
    );
  });

  it("shows one value as its text under its column name", async () => {
    openDrawer(agentRow({ [QUESTION_ID]: "Name a colour" }));
    expect(await screen.findByText("question")).toBeInTheDocument();
    expect(screen.getByText("Name a colour")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /more field/ })).toBeNull();
    expect(document.body.textContent).not.toContain(QUESTION_ID);
  });

  it("keeps an id no dataset column names as the id", async () => {
    openDrawer(agentRow({ "col-not-in-dataset": "kept" }));
    expect(await screen.findByText("col-not-in-dataset")).toBeInTheDocument();
    expect(screen.getByText("kept")).toBeInTheDocument();
  });

  it("leaves a prompt run row's named inputs as they were", async () => {
    openDrawer({ ...agentRow({}), inputs: { question: "Hi there" } });
    expect(await screen.findByText("question")).toBeInTheDocument();
    expect(screen.getByText("Hi there")).toBeInTheDocument();
    expect(outputCard.mock.calls.at(-1)[0].row.inputs).toEqual({
      question: "Hi there",
    });
  });

  it("leads with the grid's preview field read by column name", () => {
    const client = queryClient();
    const rows = [
      agentRow({ [QUESTION_ID]: "A?", [CONTEXT_ID]: "same" }),
      {
        ...agentRow({ [QUESTION_ID]: "B?", [CONTEXT_ID]: "same" }),
        example_id: "e2",
      },
    ];
    client.setQueryData([...runResultsKey(DATASET), "grid", "page-0"], {
      rows,
    });
    const { result } = renderHook(
      () =>
        usePreviewField(
          DATASET,
          rows[0],
          ["e1", "e2"],
          columnNamesById(COLUMNS),
        ),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    expect(result.current.field).toBe("question");
  });
});

describe("Raw JSON values in the grid's Input cell", () => {
  it("shows the stored envelope, column ids and all", () => {
    const stored = { values: { [QUESTION_ID]: "What is 2 + 2?" } };
    render(
      <InputCell
        data={{ example_id: "e1", inputs: stored }}
        context={{
          valueFormat: "raw",
          inputNames: columnNamesById(COLUMNS),
        }}
      />,
    );
    expect(document.body.textContent).toContain(JSON.stringify(stored));
    expect(document.body.textContent).not.toContain('"question"');
  });
});
