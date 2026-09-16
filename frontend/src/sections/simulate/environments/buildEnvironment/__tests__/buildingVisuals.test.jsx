import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act } from "@testing-library/react";
import { render, screen } from "src/utils/test-utils";

import DerivingAnimation from "../building/DerivingAnimation";
import PipelineChecks from "../building/PipelineChecks";
import { DERIVING_LABEL, PIPELINE_CHECKS_COPY } from "../build.constants";
import { pipelineStatus } from "../buildPipeline.constants";

describe("DerivingAnimation", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  // The emit loop reads a mutable tick counter inside a functional setState.
  // With real timers a render flushes between ticks; under fake timers we must
  // advance one 850ms interval at a time to reproduce that per-tick flush,
  // otherwise React batches every queued updater and they all read the final
  // counter value (three copies of the last token). One tick per act mirrors
  // production exactly.
  const emitTicks = (n) => {
    for (let i = 0; i < n; i += 1) {
      act(() => vi.advanceTimersByTime(850));
    }
  };

  it("renders the idle label, the sandbox header and a zero count", () => {
    render(<DerivingAnimation />);
    expect(screen.getByText(DERIVING_LABEL.idle)).toBeInTheDocument();
    expect(screen.getByText("SANDBOX")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("lands three chips and updates the legend after three emit ticks", () => {
    render(<DerivingAnimation />);
    emitTicks(3);

    expect(screen.getByText("verify_identity")).toBeInTheDocument();
    expect(screen.getByText("return-window rule")).toBeInTheDocument();
    expect(screen.getByText("lookup_order")).toBeInTheDocument();

    // The legend is three separate MiniCount nodes, not one string.
    expect(screen.getByText("2 tools")).toBeInTheDocument();
    expect(screen.getByText("1 rules")).toBeInTheDocument();
    expect(screen.getByText("0 tables")).toBeInTheDocument();
  });

  it("caps the emitted count at twelve", () => {
    render(<DerivingAnimation />);
    emitTicks(20);

    // The 12th (last) token has landed and the legend totals the full set.
    expect(screen.getByText("get_refund_quote")).toBeInTheDocument();
    expect(screen.getByText("6 tools")).toBeInTheDocument();
    expect(screen.getByText("3 rules")).toBeInTheDocument();
    expect(screen.getByText("3 tables")).toBeInTheDocument();

    // "12" appears twice: the source-panel line number and the sandbox count.
    expect(screen.getAllByText("12")).toHaveLength(2);
  });

  it("clears the emit interval on unmount", () => {
    const { unmount } = render(<DerivingAnimation />);
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("PipelineChecks", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("shows the running step with a live elapsed clock and the next as a ghost row", () => {
    render(<PipelineChecks pipeline={pipelineStatus([], true)} />);

    expect(screen.getByText(PIPELINE_CHECKS_COPY.heading)).toBeInTheDocument();
    expect(screen.getByText("0 of 7")).toBeInTheDocument();

    expect(screen.getByText("Understanding agent")).toBeInTheDocument();
    expect(screen.getByText("0.0s")).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(300));
    expect(screen.getByText("0.3s")).toBeInTheDocument();

    // The next queued step peeks in as a ghost row.
    expect(screen.getByText("Generating environment")).toBeInTheDocument();
    expect(screen.getByText(PIPELINE_CHECKS_COPY.queued)).toBeInTheDocument();

    // But the step after that is not revealed yet.
    expect(screen.queryByText("Building environment")).not.toBeInTheDocument();
  });

  it("renders all seven done rows with their durations", () => {
    render(<PipelineChecks pipeline={pipelineStatus(["understand", "build", "scenarios"], false)} />);

    expect(screen.getByText("7 of 7")).toBeInTheDocument();
    expect(screen.getByText("2.4s")).toBeInTheDocument();
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
