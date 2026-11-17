/* eslint-disable react/prop-types */
// The experiment Compare tab in this release: saving reviews isn't
// available. The three votes under the output cards stay on screen turned
// off and show no key, the strip says so once, no click or key 1, 2 or 3
// sends a review request, and nothing on the strip reads as saved.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import { enqueueSnackbar } from "notistack";

const requests = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: requests,
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  enqueueSnackbar: vi.fn(),
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
vi.mock("src/api/annotation-labels/annotation-labels", () => ({
  useAnnotationLabelsList: () => ({
    data: {
      results: [
        { id: "pref", name: "Preference", settings: { kind: "pairwise" } },
      ],
    },
    isLoading: false,
  }),
}));
// Only the pair vote strip renders for real; the rest of the tab is stubbed
// (each stub keeps the module's other exports).
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

import { datasetColumnsKey } from "src/sections/develop/useDatasetColumnsLite";
import ResultCompareTab from "../ResultCompareTab";
import { myPairVotes } from "../usePairVote";
import { useReviewMarks } from "../../reviewMarks";

const DATASET = "8b1c0f6e-0000-4000-8000-00000000d5e9";
const SENTENCE = "Saving reviews isn't available in this release.";
const RUN_A = { run_id: "r1", label: "#1A", name: "Support bot v1" };
const RUN_B = { run_id: "r2", label: "#2B", name: "Support bot v2" };
const ROW = {
  example_id: "e1",
  inputs: { question: "Where is my refund?" },
  expected: { value: null, reason: "no_reference_column" },
  results: {
    r1: {
      status: "completed",
      output: "Refund issued",
      trials_detail: [{ output: "Refund issued" }],
    },
    r2: {
      status: "completed",
      output: "Refund issued on 3 March",
      trials_detail: [{ output: "Refund issued on 3 March" }],
    },
  },
};
const clients = [];

// Two runs in view: the baseline #1A and the focused #2B.
function openCompare() {
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
        row={ROW}
        runs={[RUN_A, RUN_B]}
        baselineId="r1"
        focusedRunId="r2"
        trial={1}
        summary={{ metrics: [] }}
        colors={{}}
        diff="off"
        onDiffChange={vi.fn()}
        onTrialChange={vi.fn()}
        onOpenTab={vi.fn()}
        rowIds={["e1"]}
      />
    </QueryClientProvider>,
  );
}

const voteButtons = () =>
  ["A", "tie", "B"].map((choice) =>
    screen.getByTestId(`compare-vote-${choice}`),
  );
const reviewRequests = () =>
  requests.post.mock.calls.filter(([url]) =>
    String(url).includes("result-reviews"),
  );
const saveToasts = () =>
  enqueueSnackbar.mock.calls.filter(([text]) => /sav/i.test(String(text)));
// Lets any request a key or click started reach the network layer.
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

beforeEach(() => {
  vi.clearAllMocks();
  requests.get.mockResolvedValue({ data: { status: true, result: {} } });
  requests.post.mockResolvedValue({ data: { status: true, result: {} } });
  myPairVotes.clear();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  myPairVotes.clear();
});

describe("ResultCompareTab pair vote: saving reviews isn't available in this release", () => {
  it("the three votes stay on screen, turned off, show no key and are described by the sentence shown once", () => {
    openCompare();
    const votes = voteButtons();
    votes.forEach((vote) => {
      expect(vote).toBeVisible();
      expect(vote).toBeDisabled();
      expect(vote).toHaveAccessibleDescription(SENTENCE);
    });
    expect(votes.map((vote) => vote.textContent)).toEqual([
      "#1A is better",
      "Tie",
      "#2B is better",
    ]);
    expect(screen.getAllByText(SENTENCE)).toHaveLength(1);
  });

  it("a click on each vote and keys 1, 2 and 3 send no review request", async () => {
    openCompare();
    voteButtons().forEach((vote) => fireEvent.click(vote));
    ["1", "2", "3"].forEach((key) =>
      fireEvent.keyDown(document.body, { key, bubbles: true }),
    );
    await settle();
    expect(reviewRequests()).toEqual([]);
  });

  it("after a click or a key nothing reads as saved and no vote shows as picked", async () => {
    openCompare();
    fireEvent.click(screen.getByTestId("compare-vote-B"));
    fireEvent.keyDown(document.body, { key: "2", bubbles: true });
    await settle();
    expect(screen.queryByText(/\bsaved\b/i)).toBeNull();
    expect(screen.queryByText("Saving…")).toBeNull();
    expect(saveToasts()).toEqual([]);
    voteButtons().forEach((vote) =>
      expect(vote).not.toHaveAttribute("aria-pressed", "true"),
    );
    expect(myPairVotes.size).toBe(0);
    expect(screen.getByText(SENTENCE)).toBeInTheDocument();
  });

  it("after a click or a key the grid's row marks stay empty", async () => {
    openCompare();
    fireEvent.click(screen.getByTestId("compare-vote-B"));
    fireEvent.keyDown(document.body, { key: "2", bubbles: true });
    await settle();
    expect(useReviewMarks.getState().marks).toEqual({});
  });
});
