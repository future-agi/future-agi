/* eslint-disable react/prop-types */
import React, { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";

const requests = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: requests,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), Events: {} }));
vi.mock("src/sections/common/ValueViewer/ValueViewer", () => ({
  default: ({ value }) => (
    <div>{typeof value === "string" ? value : JSON.stringify(value)}</div>
  ),
}));
// Keep ResultCompareTab, trialOutcome, OutputCard, trialNote and the real
// trial stepper connected; unrelated panels do not participate in this test.
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
vi.mock("src/sections/common/Runs/RunChip", async (original) => ({
  ...(await original()),
  default: ({ run }) => <span>{run.label}</span>,
}));
vi.mock(
  "src/sections/experiment-detail/results/RunSourceBlock",
  async (original) => ({
    ...(await original()),
    useRunWithConfig: (run) => ({ full: run }),
  }),
);

import { datasetColumnsKey } from "src/sections/develop/useDatasetColumnsLite";
import { trialOutcome } from "../../resultsSimple";
import cases from "../../__tests__/trialOutcome.fixture.json";
import ResultCompareTab from "../ResultCompareTab";

const DATASET = "8b1c0f6e-0000-4000-8000-00000000d5e8";
const RUN = { run_id: "run-verdicts", label: "#1A", name: "Three tries" };
const METRIC = { key: "verdict", kind: "eval", output_type: "pass_fail" };
// The backend projection test verifies this score in both full and compact
// responses: the unassessed middle slot is present on the wire.
const { score } = cases.find(
  (entry) => entry.name === "an unassessed try between verdicts",
);
const RESULT = {
  status: "completed",
  scores: { verdict: score },
  trials_detail: [1, 2, 3].map((number) => ({
    trial_number: number,
    status: "completed",
    output: `Answer from try ${number}`,
  })),
};
const ROW = {
  example_id: "example-verdicts",
  inputs: { question: "Name a colour" },
  results: { [RUN.run_id]: RESULT },
};
const clients = [];

function Compare() {
  const [trial, setTrial] = useState(1);
  return (
    <ResultCompareTab
      datasetId={DATASET}
      row={ROW}
      runs={[RUN]}
      baselineId={RUN.run_id}
      focusedRunId={RUN.run_id}
      trial={trial}
      summary={{ metrics: [METRIC] }}
      colors={{}}
      diff="off"
      onDiffChange={vi.fn()}
      onTrialChange={setTrial}
      onOpenTab={vi.fn()}
      readOnly
      rowIds={[ROW.example_id]}
    />
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

describe("drawer verdicts follow their original tries", () => {
  it("leaves an unassessed middle try unlabelled when stepping through outputs", () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    client.setQueryData(datasetColumnsKey(DATASET), {
      data: { result: { columns: [] } },
    });
    clients.push(client);
    render(
      <QueryClientProvider client={client}>
        <Compare />
      </QueryClientProvider>,
    );

    const expectTry = (number, note) => {
      expect(screen.getByTestId("output-card-trial").textContent).toBe(note);
      expect(screen.getByText(`Answer from try ${number}`).textContent).toBe(
        `Answer from try ${number}`,
      );
      const { pass, n, dots } = trialOutcome(RESULT, METRIC.key);
      expect({ pass, n, dots }).toEqual({
        pass: 1,
        n: 2,
        dots: [false, true],
      });
    };
    expectTry(1, "trial 1 of 3 · the failing one");
    fireEvent.click(screen.getByRole("button", { name: "Next trial" }));
    expectTry(2, "trial 2 of 3");
    fireEvent.click(screen.getByRole("button", { name: "Next trial" }));
    expectTry(3, "trial 3 of 3 · the passing one");
    fireEvent.click(screen.getByRole("button", { name: "Previous trial" }));
    expectTry(2, "trial 2 of 3");
  });
});
