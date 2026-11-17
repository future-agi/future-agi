import React from "react";
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import EvaluationsContent from "../EvaluationsContent";

// The session detail read (tracer/views/trace_session.py) sends `score: null`
// plus `error: true` or a lifecycle `status` for evals with no completed score.
// The trace card must show the shared Error / Queued / Evaluating / Skipped
// indicators for those, never a green "<name>: —" line that reads as a pass.

const p = palette("light");
const SUCCESS = p.status.success.fg;
const DANGER = p.status.danger.fg;

const renderMetrics = (evaluationMetrics) =>
  render(<EvaluationsContent evaluationMetrics={evaluationMetrics} />);

const NOT_SCORED = {
  errored: {
    score: null,
    name: "Toxicity",
    explanation: null,
    error: true,
  },
  pending: {
    score: null,
    name: "Relevance",
    explanation: null,
    status: "pending",
  },
  running: {
    score: null,
    name: "Faithfulness",
    explanation: null,
    status: "running",
  },
  skipped: {
    score: null,
    name: "Context recall",
    explanation: "missing: context",
    status: "skipped",
    skipped_reason: "missing: context",
  },
};

describe("EvaluationsContent (session trace card)", () => {
  it("keeps completed scores as '<name>: <score>%' in success and danger colors", () => {
    renderMetrics({
      a: { score: 85, name: "Accuracy", explanation: null },
      b: { score: 20, name: "Bias", explanation: null },
    });
    expect(screen.getByText("Accuracy: 85%")).toHaveStyle({ color: SUCCESS });
    expect(screen.getByText("Bias: 20%")).toHaveStyle({ color: DANGER });
  });

  it("shows the Error chip for an eval that errored", () => {
    renderMetrics({ c1: NOT_SCORED.errored });
    expect(screen.getByText("Toxicity:")).toBeInTheDocument();
    expect(screen.getByText("Error")).toBeInTheDocument();
    expect(screen.queryByText("Toxicity: —")).not.toBeInTheDocument();
  });

  it("shows the Queued pill for a pending eval", () => {
    renderMetrics({ c2: NOT_SCORED.pending });
    expect(screen.getByText("Relevance:")).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(screen.queryByText("Relevance: —")).not.toBeInTheDocument();
  });

  it("shows the loading skeleton, labelled for screen readers, for a running eval", () => {
    const { container } = renderMetrics({ c3: NOT_SCORED.running });
    expect(screen.getByText("Faithfulness:")).toBeInTheDocument();
    expect(container.querySelector(".MuiSkeleton-root")).toBeInTheDocument();
    expect(screen.getByText("Evaluating…")).toBeInTheDocument();
    expect(screen.queryByText("Faithfulness: —")).not.toBeInTheDocument();
  });

  it("shows the Skipped chip with its reason on hover for a skipped eval", async () => {
    renderMetrics({ c4: NOT_SCORED.skipped });
    expect(screen.getByText("Context recall:")).toBeInTheDocument();
    expect(screen.queryByText("Context recall: —")).not.toBeInTheDocument();
    const chip = screen.getByText("Skipped");
    // The reason lives in the chip's hover tooltip, not inline.
    expect(screen.queryByText("missing: context")).not.toBeInTheDocument();
    fireEvent.mouseOver(chip);
    expect(await screen.findByText("missing: context")).toBeInTheDocument();
  });

  it("never paints a not-yet-scored eval name in the success color", () => {
    renderMetrics({
      c1: NOT_SCORED.errored,
      c2: NOT_SCORED.pending,
      c3: NOT_SCORED.running,
      c4: NOT_SCORED.skipped,
    });
    for (const name of [
      /^Toxicity/,
      /^Relevance/,
      /^Faithfulness/,
      /^Context recall/,
    ]) {
      const label = screen.getByText(name);
      expect(label).toHaveStyle({ color: p.text.primary });
      expect(label).not.toHaveStyle({ color: SUCCESS });
    }
  });

  it("renders a missing score as a neutral '—', not in the success color", () => {
    renderMetrics({ c5: { score: null, name: "Tone", explanation: null } });
    const line = screen.getByText("Tone: —");
    expect(line).toHaveStyle({ color: p.text.secondary });
    expect(line).not.toHaveStyle({ color: SUCCESS });
  });
});
