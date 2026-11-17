/* eslint-disable react/prop-types */
import React from "react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import { runResultsKey } from "src/api/develop/run-results";
import ScoreCard from "../ScoreCard";

vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), Events: {} }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { name: "Current viewer" } }),
}));
vi.mock("src/sections/common/Runs/RunChip", () => ({ default: () => null }));
vi.mock("src/sections/common/Metrics/DirectionIcon", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/EvalTypes/TypedScoreDetails", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/DecisionJudges/DecisionCallPanel", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/EvalHandoffs/OpenInEvalsLink", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/SafeMarkdown/ReasonText", () => ({
  default: ({ text }) => <div>{text}</div>,
}));

const run = {
  run_id: "061d3901-f278-4763-8199-922b7248e132",
  experiment_id: "352fbb14-b689-49e7-8df4-4c00136df713",
  dataset_id: "fa20d99c-2c03-493a-b46b-e904de42f120",
};
const metricId = "a9caf794-4bea-483d-a1b5-848b21bd3a47";
const rowId = "781d923f-c94f-4a34-b43a-986b272e4426";
const columnId = "ba1abac7-808f-499c-aa4f-7b7dd031264a";
const itemId = "fc18275e-f701-4219-8249-abcbdaec1514";
const def = {
  key: metricId,
  name: "Answer quality",
  output_type: "score",
  uem_ids: { [run.run_id]: metricId },
  feedback_column_ids: { [run.run_id]: columnId },
};
const initial = {
  result_id: itemId,
  feedback_row_id: rowId,
  scores: { [metricId]: { value: 0.2 } },
};

function ResultsCard() {
  const query = useQuery({
    queryKey: [...runResultsKey(run.dataset_id), "row", rowId],
    queryFn: async () => (await axios.get("/results-row")).data,
  });
  if (!query.data) return null;
  return (
    <ScoreCard
      def={def}
      run={run}
      result={query.data}
      uemId={def.uem_ids[run.run_id]}
      trial={1}
    />
  );
}

const renderCard = (card) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{card}</QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Score card feedback", () => {
  it("posts the snapshot row and evaluation column then reads the saved explanation", async () => {
    let current = initial;
    axios.get.mockImplementation(async () => ({ data: current }));
    axios.post.mockImplementation(async (_, body) => {
      current = {
        ...initial,
        scores: {
          [metricId]: {
            value: 0.2,
            override: {
              value: body.value,
              explanation: body.explanation,
              at: new Date().toISOString(),
              user_name: "Reviewer",
            },
          },
        },
      };
      return { data: { result: { id: "saved-feedback" } } };
    });
    renderCard(<ResultsCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Override" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Corrected score" }), {
      target: { value: "0.8" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Comment" }), {
      target: { value: "The answer matches the reference." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        endpoints.develop.experiment.feedback.create(run.experiment_id),
        {
          source: "experiment",
          source_id: columnId,
          row_id: rowId,
          user_eval_metric: metricId,
          value: "0.8",
          explanation: "The answer matches the reference.",
        },
      ),
    );
    expect(
      await screen.findByText("The answer matches the reference."),
    ).toBeInTheDocument();
    expect(screen.getByTestId("score-card-value")).toHaveTextContent("0.8");
    expect(screen.getByText(/Human: 0.8 \(was 0.20\)/)).toHaveTextContent(
      "Reviewer",
    );
    expect(screen.getByRole("button", { name: "Correct again" })).toBeEnabled();
    expect(axios.get.mock.calls.length).toBeGreaterThan(1);
  });

  it("shows the stored correction and explanation when reopening a result", () => {
    renderCard(
      <ScoreCard
        def={def}
        run={run}
        result={{
          ...initial,
          scores: {
            [metricId]: {
              value: 0.2,
              override: {
                value: "0.9",
                explanation: "Verified against the expected answer.",
                at: new Date().toISOString(),
                user_name: "Another reviewer",
              },
            },
          },
        }}
        uemId={metricId}
        trial={1}
      />,
    );
    expect(screen.getByTestId("score-card-value")).toHaveTextContent("0.9");
    expect(
      screen.getByText("Verified against the expected answer."),
    ).toBeInTheDocument();
    expect(screen.getByText(/Human:/)).toHaveTextContent("Another reviewer");
  });

  it("does not use a result item as a snapshot row when identity is unavailable", () => {
    renderCard(
      <ScoreCard
        def={def}
        run={run}
        result={{ result_id: itemId, scores: initial.scores }}
        uemId={metricId}
        trial={1}
      />,
    );
    expect(screen.getByRole("button", { name: "Override" })).toBeDisabled();
    expect(axios.post).not.toHaveBeenCalled();
  });
});

describe("Score card feedback on a choice eval", () => {
  // A deterministic eval with a label map projects as output_type "choice",
  // and its cells read the chosen label (results_catalog.metric_shape).
  const choiceDef = {
    ...def,
    output_type: "choice",
    choice_scores: { Pass: 1, Fail: 0 },
  };
  const choiceResult = { ...initial, scores: { [metricId]: { value: 0 } } };

  it("offers the eval's labels and saves the picked one", async () => {
    axios.post.mockResolvedValue({
      data: { result: { id: "saved-feedback" } },
    });
    renderCard(
      <ScoreCard
        def={choiceDef}
        run={run}
        result={choiceResult}
        uemId={metricId}
        trial={1}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Override" }));
    expect(
      screen.queryByRole("textbox", { name: "Corrected score" }),
    ).toBeNull();
    const save = screen.getByRole("button", { name: "Save correction" });
    expect(save).toBeDisabled();
    fireEvent.mouseDown(
      screen.getByRole("combobox", { name: "Corrected label" }),
    );
    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["Pass", "Fail"]);
    fireEvent.click(screen.getByRole("option", { name: "Pass" }));
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        endpoints.develop.experiment.feedback.create(run.experiment_id),
        {
          source: "experiment",
          source_id: columnId,
          row_id: rowId,
          user_eval_metric: metricId,
          value: "Pass",
          explanation: "",
        },
      ),
    );
  });

  it("keeps the score field when the eval's labels are unknown", () => {
    renderCard(
      <ScoreCard
        def={{ ...choiceDef, choice_scores: null }}
        run={run}
        result={choiceResult}
        uemId={metricId}
        trial={1}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Override" }));
    expect(
      screen.getByRole("textbox", { name: "Corrected score" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: "Corrected label" }),
    ).toBeNull();
  });
});
