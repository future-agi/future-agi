import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import EvalRollupSection from "../index";

const scores = {
  scope: "trace",
  evals: [
    {
      eval_config_id: "c1",
      eval_name: "groundedness",
      output_type: "score",
      target_type: "spans",
      aggregate: 80,
      spans: [{ span_id: "s1", span_name: "a", value: 80 }],
    },
    {
      eval_config_id: "c2",
      eval_name: "toxicity",
      output_type: "pass/fail",
      target_type: "traces",
      aggregate: { pass: 1, fail: 0 },
      spans: [{ span_id: "s1", span_name: "a", value: "pass" }],
    },
    {
      eval_config_id: "c3",
      eval_name: "tone",
      output_type: "choices",
      target_type: "sessions",
      choices_map: { Accurate: "pass" },
      aggregate: { Accurate: 1 },
      spans: [{ span_id: "s1", span_name: "a", value: ["Accurate"] }],
    },
  ],
};

describe("EvalRollupSection renders a flat eval list", () => {
  it("renders every eval with no task header", () => {
    render(<EvalRollupSection evalScores={scores} />);
    expect(screen.getByText("groundedness")).toBeInTheDocument();
    expect(screen.getByText("toxicity")).toBeInTheDocument();
    expect(screen.getByText("tone")).toBeInTheDocument();
  });

  it("renders S for a spans eval and T for a traces eval", () => {
    render(<EvalRollupSection evalScores={scores} />);
    expect(screen.getByText("S")).toBeInTheDocument();
    expect(screen.getByText("T")).toBeInTheDocument();
  });

  it("renders no glyph for a sessions eval — only S and T appear", () => {
    render(<EvalRollupSection evalScores={scores} />);
    expect(screen.queryAllByText(/^(S|T)$/)).toHaveLength(2);
  });

  it("showGlyph={false} suppresses every glyph", () => {
    render(<EvalRollupSection evalScores={scores} showGlyph={false} />);
    expect(screen.queryByText("S")).not.toBeInTheDocument();
    expect(screen.queryByText("T")).not.toBeInTheDocument();
  });

  it("renders the empty state when evals is empty", () => {
    render(
      <EvalRollupSection evalScores={{ scope: "trace", evals: [] }} />,
    );
    expect(screen.getByText("No evaluations available")).toBeInTheDocument();
  });

  it("typing a query that matches one eval name hides the rest", () => {
    render(<EvalRollupSection evalScores={scores} />);
    const input = screen.getByPlaceholderText("Search evals...");
    fireEvent.change(input, { target: { value: "tox" } });
    expect(screen.getByText("toxicity")).toBeInTheDocument();
    expect(screen.queryByText("groundedness")).not.toBeInTheDocument();
    expect(screen.queryByText("tone")).not.toBeInTheDocument();
  });

  it("typing a query that matches nothing shows the no-results message", () => {
    render(<EvalRollupSection evalScores={scores} />);
    const input = screen.getByPlaceholderText("Search evals...");
    fireEvent.change(input, { target: { value: "zzz-no-match" } });
    expect(screen.getByText("No evals match your search")).toBeInTheDocument();
    expect(screen.queryByText("groundedness")).not.toBeInTheDocument();
  });

  it("search also matches on span name, not just eval name", () => {
    render(<EvalRollupSection evalScores={scores} />);
    const input = screen.getByPlaceholderText("Search evals...");
    // Every fixture eval's only span is named "a"; none of the eval names
    // contain "a", so a hit here proves the span_name clause matched.
    fireEvent.change(input, { target: { value: "a" } });
    expect(screen.getByText("groundedness")).toBeInTheDocument();
    expect(screen.getByText("toxicity")).toBeInTheDocument();
    expect(screen.getByText("tone")).toBeInTheDocument();
  });
});
