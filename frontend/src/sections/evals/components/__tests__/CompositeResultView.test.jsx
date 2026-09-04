import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CompositeResultView from "../CompositeResultView";

const baseChild = {
  child_id: "child-1",
  child_name: "Toxicity Check",
  order: 0,
  weight: 1,
  status: "completed",
  score: 0.875,
};

describe("CompositeResultView", () => {
  it("renders the aggregate score, aggregation label and pass/fail chip when aggregation is enabled", () => {
    render(
      <CompositeResultView
        compositeResult={{
          aggregation_enabled: true,
          aggregation_function: "weighted_avg",
          aggregate_score: 0.8234,
          aggregate_pass: true,
          total_children: 1,
          completed_children: 1,
          failed_children: 0,
          children: [baseChild],
        }}
      />,
    );

    expect(
      screen.getByText(/Aggregate Score \(Weighted Average\)/),
    ).toBeInTheDocument();
    expect(screen.getByText("0.823")).toBeInTheDocument();
    expect(screen.getByText("PASS")).toBeInTheDocument();
  });

  it("renders a FAIL chip when aggregate_pass is false", () => {
    render(
      <CompositeResultView
        compositeResult={{
          aggregation_enabled: true,
          aggregation_function: "avg",
          aggregate_score: 0.2,
          aggregate_pass: false,
          total_children: 1,
          completed_children: 1,
          failed_children: 0,
          children: [baseChild],
        }}
      />,
    );

    expect(screen.getByText("FAIL")).toBeInTheDocument();
  });

  it("shows the disabled-aggregation message and no aggregate score when aggregation is off", () => {
    render(
      <CompositeResultView
        compositeResult={{
          aggregation_enabled: false,
          total_children: 1,
          completed_children: 1,
          failed_children: 0,
          children: [baseChild],
        }}
      />,
    );

    expect(
      screen.getByText(
        "Aggregation disabled — individual child results only",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("PASS")).not.toBeInTheDocument();
    expect(screen.queryByText("FAIL")).not.toBeInTheDocument();
  });

  it("shows the no-aggregate-score message when aggregation is enabled but no score was produced", () => {
    render(
      <CompositeResultView
        compositeResult={{
          aggregation_enabled: true,
          aggregation_function: "avg",
          aggregate_score: null,
          total_children: 1,
          completed_children: 0,
          failed_children: 1,
          children: [{ ...baseChild, status: "failed", score: null, error: "boom" }],
        }}
      />,
    );

    expect(
      screen.getByText(
        "No aggregate score (no children produced a normalized score)",
      ),
    ).toBeInTheDocument();
  });

  it("renders one card per child with name, order, weight and score", () => {
    render(
      <CompositeResultView
        compositeResult={{
          aggregation_enabled: true,
          aggregation_function: "weighted_avg",
          aggregate_score: 0.6,
          total_children: 2,
          completed_children: 2,
          failed_children: 0,
          children: [
            { ...baseChild, child_id: "c1", child_name: "Toxicity", order: 0, weight: 2, score: 0.9 },
            { ...baseChild, child_id: "c2", child_name: "Relevance", order: 1, weight: 1, score: 0.3 },
          ],
        }}
      />,
    );

    expect(screen.getByText("Toxicity")).toBeInTheDocument();
    expect(screen.getByText("Relevance")).toBeInTheDocument();
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText("#2")).toBeInTheDocument();
    expect(screen.getByText("w: 2")).toBeInTheDocument();
    expect(screen.getByText("0.900")).toBeInTheDocument();
    expect(screen.getByText("0.300")).toBeInTheDocument();
  });

  it("does not render a weight chip for the default weight of 1", () => {
    render(
      <CompositeResultView
        compositeResult={{
          aggregation_enabled: false,
          total_children: 1,
          completed_children: 1,
          failed_children: 0,
          children: [{ ...baseChild, weight: 1 }],
        }}
      />,
    );

    expect(screen.queryByText(/^w: /)).not.toBeInTheDocument();
  });

  it("renders a child's error state distinctly from a successful child", () => {
    render(
      <CompositeResultView
        compositeResult={{
          aggregation_enabled: false,
          total_children: 2,
          completed_children: 1,
          failed_children: 1,
          children: [
            { ...baseChild, child_id: "ok", child_name: "Passing child", status: "completed", score: 0.9 },
            {
              child_id: "bad",
              child_name: "Broken child",
              order: 1,
              status: "failed",
              score: null,
              error: "Timed out calling judge model",
            },
          ],
        }}
      />,
    );

    expect(
      screen.getByText("Error: Timed out calling judge model"),
    ).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getByText("completed")).toBeInTheDocument();
    expect(screen.getByText("1 failed")).toBeInTheDocument();
  });

  it("renders a child's reason as markdown text", () => {
    render(
      <CompositeResultView
        compositeResult={{
          aggregation_enabled: false,
          total_children: 1,
          completed_children: 1,
          failed_children: 0,
          children: [
            { ...baseChild, reason: "This response scored well because it was concise." },
          ],
        }}
      />,
    );

    expect(
      screen.getByText("This response scored well because it was concise."),
    ).toBeInTheDocument();
  });

  it("renders the completed/total count and an empty child list gracefully", () => {
    render(
      <CompositeResultView
        compositeResult={{
          aggregation_enabled: false,
          total_children: 0,
          completed_children: 0,
          failed_children: 0,
          children: [],
        }}
      />,
    );

    expect(screen.getByText("0/0 completed")).toBeInTheDocument();
  });

  it("renders without throwing when compositeResult is undefined", () => {
    render(<CompositeResultView />);

    expect(screen.getByText("Child Evaluations")).toBeInTheDocument();
  });
});
