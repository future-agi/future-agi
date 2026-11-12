// An unexpected error stores the server's catch-all text, which only says to
// contact support. The score cell's tooltip and the row drawer's Reason say in
// one sentence of ours what to do next, and never echo the stored text.
/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { ScoreCellRenderer } from "../../cells/MetricCells";
import ScoreCard from "../ScoreCard";

vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), Events: {} }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { name: "Tester" } }),
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
  default: ({ text }) => <div data-testid="reason-text">{text}</div>,
}));

const NEXT_STEP =
  "Not scored: the evaluation failed unexpectedly. Run it again; if it fails again, contact Future AGI support. It is left out of the pass rate.";
const CATCH_ALL = [
  "Evaluation failed. Please contact Future AGI support.",
  "Evaluation failed. Please contact Future AGI support",
  "Unable to process row. Please try again or contact Future AGI support.",
];
const RUN = "run-1";
const METRIC = "metric-judge";
const def = { key: METRIC, name: "Judge", output_type: "score" };

const failed = (explanation) => ({
  value: null,
  status: "not_assessed",
  n: 0,
  error_trials: 1,
  explanation,
});

const renderCell = (explanation) =>
  render(
    <ScoreCellRenderer
      data={{
        example_id: "e1",
        expected: { value: null },
        results: {
          [RUN]: {
            status: "completed",
            scores: { [METRIC]: failed(explanation) },
          },
        },
      }}
      context={{
        defsByKey: { [METRIC]: def },
        runsById: { [RUN]: { run_id: RUN, status: "Completed" } },
      }}
      runId={RUN}
      metricKey={METRIC}
    />,
  );

const renderCard = (explanation) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ScoreCard
        def={def}
        run={{ run_id: "r1", experiment_id: "x1", dataset_id: "d1" }}
        result={{
          result_id: "r1:e1",
          scores: { [METRIC]: failed(explanation) },
          trials_detail: [
            {
              trial_number: 1,
              status: "completed",
              scores: {
                [METRIC]: {
                  value: null,
                  status: "error",
                  passed: null,
                  evaluator_version_id: "v1",
                  explanation,
                },
              },
            },
          ],
        }}
        trial={1}
        readOnly
      />
    </QueryClientProvider>,
  );

describe("A failed evaluation that stored the server's catch-all text", () => {
  it.each(CATCH_ALL)("says what to do in the tooltip: %s", (text) => {
    renderCell(text);
    const tip = screen
      .getByText("Not scored · the evaluation failed")
      .getAttribute("aria-label");
    expect(tip).toBe(NEXT_STEP);
  });

  it.each(CATCH_ALL)("says what to do under Reason: %s", (text) => {
    renderCard(text);
    expect(screen.getByTestId("reason-text")).toHaveTextContent(NEXT_STEP);
    expect(document.body.innerHTML).not.toContain("Please contact");
  });

  it("keeps a specific reason that mentions support as its own sentence", () => {
    const own =
      "The judge model refused the request. Please contact Future AGI support.";
    renderCard(own);
    expect(screen.getByTestId("reason-text")).toHaveTextContent(
      `The evaluation failed: ${own}`,
    );
  });
});
