import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act } from "@testing-library/react";
import { render, screen } from "src/utils/test-utils";

import DerivingAnimation from "../building/DerivingAnimation";
import PipelineChecks from "../building/PipelineChecks";
import { DERIVING_LABEL, PIPELINE_CHECKS_COPY } from "../build.constants";
import { pipelineStatus } from "../buildPipeline.constants";

describe("DerivingAnimation", () => {
  const WORLD = {
    tools: [{ name: "verify_identity" }, { name: "issue_refund" }],
    rules: ["The fix must not edit the test files."],
    seed: { tables: [{ name: "customers", rows: 240 }] },
  };

  it("shows the real source and a neutral skeleton (no invented names) before the world lands", () => {
    render(<DerivingAnimation source="ride-voice-agent" />);
    expect(screen.getByText("SANDBOX")).toBeInTheDocument();
    // the real source in the file header — not the old hardcoded filename
    expect(screen.getByText("ride-voice-agent")).toBeInTheDocument();
    expect(screen.queryByText("handlers/refunds.py")).not.toBeInTheDocument();
    // no canned token chips, and the legend is genuinely zero
    expect(screen.queryByText("verify_identity")).not.toBeInTheDocument();
    expect(screen.getByText("0 tools")).toBeInTheDocument();
    expect(screen.getByText("0 rules")).toBeInTheDocument();
    expect(screen.getByText("0 tables")).toBeInTheDocument();
  });

  it("renders the real derived tools/tables and real counts once the world is available", () => {
    render(<DerivingAnimation source="ride-voice-agent" world={WORLD} />);
    expect(screen.getByText("verify_identity")).toBeInTheDocument();
    expect(screen.getByText("issue_refund")).toBeInTheDocument();
    expect(screen.getByText("customers × 240")).toBeInTheDocument();
    // legend totals the full real world (rules counted even if clipped as a chip)
    expect(screen.getByText("2 tools")).toBeInTheDocument();
    expect(screen.getByText("1 rules")).toBeInTheDocument();
    expect(screen.getByText("1 tables")).toBeInTheDocument();
  });

  it("falls back to a neutral source label and the idle phase line when nothing is passed", () => {
    render(<DerivingAnimation />);
    expect(screen.getByText(DERIVING_LABEL.readingSource)).toBeInTheDocument();
    expect(screen.getByText(DERIVING_LABEL.idle)).toBeInTheDocument();
  });
});

describe("PipelineChecks", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("shows the running step without a client-side clock and the next as a ghost row", () => {
    render(<PipelineChecks pipeline={pipelineStatus([], true)} />);

    expect(screen.getByText(PIPELINE_CHECKS_COPY.heading)).toBeInTheDocument();
    expect(screen.getByText("0 of 7")).toBeInTheDocument();

    expect(screen.getByText("Understanding agent")).toBeInTheDocument();
    // The backend has no per-step start time, so no elapsed clock is shown —
    // a mount-relative one reset to 0.0s on every revisit.
    act(() => vi.advanceTimersByTime(300));
    expect(screen.queryByText(/^\d+\.\ds$/)).not.toBeInTheDocument();

    // The next queued step peeks in as a ghost row.
    expect(screen.getByText("Generating environment")).toBeInTheDocument();
    expect(screen.getByText(PIPELINE_CHECKS_COPY.queued)).toBeInTheDocument();

    // But the step after that is not revealed yet.
    expect(screen.queryByText("Building environment")).not.toBeInTheDocument();
  });

  it("renders all seven done rows without made-up durations", () => {
    render(<PipelineChecks pipeline={pipelineStatus(["understand", "build", "scenarios"], false)} />);

    expect(screen.getByText("7 of 7")).toBeInTheDocument();
    expect(screen.queryByText(/^\d+\.\ds$/)).not.toBeInTheDocument();
    expect(screen.getByText("Validating scenarios")).toBeInTheDocument();
    expect(screen.queryByText(PIPELINE_CHECKS_COPY.queued)).not.toBeInTheDocument();
  });

  it("stops at the failed row when a step fails", () => {
    const pipeline = pipelineStatus(["understand"], true, "setup", {
      stepId: "build-env",
      title: "Building environment",
      detail: "Writing handlers failed",
      retryable: true,
    });
    render(<PipelineChecks pipeline={pipeline} />);

    expect(screen.getByText("Building environment")).toBeInTheDocument();
    expect(screen.queryByText("Validating environment")).not.toBeInTheDocument();
  });

  it("renders nothing when the pipeline is empty", () => {
    render(<PipelineChecks pipeline={[]} />);
    expect(screen.queryByText(PIPELINE_CHECKS_COPY.heading)).not.toBeInTheDocument();
  });
});
