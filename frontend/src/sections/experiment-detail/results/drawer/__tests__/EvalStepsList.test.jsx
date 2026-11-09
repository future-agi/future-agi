// W1-9 B3 (E39): the eval steps list renders the trace's verdicts for the
// focused run, failing first, expands to the details, and opens the eval's
// own trace.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import EvalStepsList from "../EvalStepsList";

const SPANS = [
  {
    observation_span: { id: "root", name: "run" },
    children: [
      {
        observation_span: { id: "s1", name: "respond" },
        eval_scores: [
          { eval_name: "Tone", score: 100, result: true, run_id: "c" },
          {
            eval_name: "Correctness",
            score: 0,
            result: false,
            run_id: "c",
            explanation: "The reply promises a refund the tool never issued.",
            judge_model: "gpt-4o",
            judge_cost_usd: 0.0004,
            eval_trace_id: "7d-eval",
            version_id: "v2",
          },
          {
            eval_name: "Groundedness",
            score: null,
            result: null,
            status: "error",
            error: true,
            run_id: "c",
            explanation: "judge returned 7 on a 0-1 scale",
          },
          { eval_name: "Correctness", score: 100, result: true, run_id: "p" },
        ],
        children: [],
      },
    ],
  },
];

vi.mock("src/sections/common/Trajectory/useTraceTree", () => ({
  default: (id) => ({ spans: id ? SPANS : null }),
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: ({ traceId }) => (
    <div data-testid="eval-trace-drawer">{traceId}</div>
  ),
}));

describe("EvalStepsList", () => {
  it("renders nothing without a trace", () => {
    const { container } = render(<EvalStepsList traceId={null} runId="c" />);
    expect(container.textContent).toBe("");
  });

  it("lists this run's verdicts failing first and opens the eval trace", () => {
    render(<EvalStepsList traceId="t1" runId="c" runLabel="#2H" />);
    const lines = screen
      .getAllByTestId("eval-step-line")
      .map((n) => n.textContent);
    expect(lines).toEqual([
      'Correctness · judge gpt-4o · Fail · "The reply promises a refund the tool never issued." · $0.0004',
      "Groundedness · Score error · judge returned 7 on a 0-1 scale",
      "Tone · Pass",
    ]);
    expect(screen.getByText(/3 verdicts on this try · 1 failing/)).toBeTruthy();
    fireEvent.click(screen.getAllByTestId("eval-step-line")[0]);
    expect(screen.getByText("Fail · 0 / 100")).toBeTruthy();
    expect(screen.getByText("respond")).toBeTruthy();
    fireEvent.click(screen.getByTestId("eval-step-open"));
    expect(screen.getByTestId("eval-trace-drawer").textContent).toBe("7d-eval");
  });

  it("review S8: a verdict with no eval trace has no Open eval details", () => {
    render(<EvalStepsList traceId="t1" runId="c" runLabel="#2H" />);
    // Tone carries no eval_trace_id
    fireEvent.click(screen.getAllByTestId("eval-step-line")[2]);
    expect(screen.queryByTestId("eval-step-open")).toBeNull();
  });
});
