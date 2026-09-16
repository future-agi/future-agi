import { within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render, screen } from "src/utils/test-utils";
import BuildHeader from "../BuildHeader";
import {
  BUILD_PIPELINE,
  PIPELINE_PHASES,
  pipelineStatus,
  pipelineSummary,
} from "../buildPipeline.constants";

// Build the exact props the section computes, from a single pipeline, so the
// test can never drift from the header's own inputs.
const propsFor = (pipeline, overrides = {}) => ({
  onBack: vi.fn(),
  name: "Support bot",
  onRename: vi.fn(),
  pipeline,
  summary: pipelineSummary(pipeline),
  setupDone: pipeline.filter((s) => s.phase === "setup").every((s) => s.status === "done"),
  onRun: vi.fn(),
  canRun: false,
  runBlockedReason: "Finish the three stages on the left first",
  ...overrides,
});

const renderHeader = (pipeline, overrides = {}) => {
  const props = propsFor(pipeline, overrides);
  render(<BuildHeader {...props} />);
  return props;
};

describe("BuildHeader", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the running step label while setup is building and no Run button", async () => {
    const props = renderHeader(pipelineStatus([], true));

    expect(screen.getByText("Understanding agent")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run simulation" })).not.toBeInTheDocument();

    const back = screen.getByRole("button", { name: "Change source" });
    await userEvent.click(back);
    expect(props.onBack).toHaveBeenCalledTimes(1);
  });

  it("falls back to 'Setup being built' when nothing is running yet", () => {
    renderHeader(pipelineStatus([], false));
    expect(screen.getByText("Setup being built")).toBeInTheDocument();
  });

  it("shows 'Ready to run' and a disabled Run button when setup is done but blocked", () => {
    renderHeader(pipelineStatus(["understand", "build", "scenarios"], false), { canRun: false });

    expect(screen.getByText("Ready to run")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run simulation" })).toBeDisabled();
  });

  it("runs the simulation when setup is done and the run is allowed", async () => {
    const props = renderHeader(pipelineStatus(["understand", "build", "scenarios"], false), {
      canRun: true,
    });

    const run = screen.getByRole("button", { name: "Run simulation" });
    expect(run).toBeEnabled();
    await userEvent.click(run);
    expect(props.onRun).toHaveBeenCalledTimes(1);
  });

  it("shows a failure label when a setup step failed", () => {
    const failure = { stepId: "build-env", title: "Docker build failed", detail: "boom", retryable: true, onRetry: vi.fn() };
    renderHeader(pipelineStatus(["understand"], false, "setup", failure));
    expect(screen.getByText("Failed at building environment")).toBeInTheDocument();
  });

  it("opens the pipeline popover from the milestone pill with every phase and step", async () => {
    const onRetry = vi.fn();
    const failure = { stepId: "build-env", title: "Docker build failed", detail: "The build step could not finish", retryable: true, onRetry };
    renderHeader(pipelineStatus(["understand"], false, "setup", failure));

    // The pill lives in a responsive `display:{xs:none,md:flex}` box; jsdom's
    // getComputedStyle drops the md override, so query by label not role.
    await userEvent.click(screen.getByLabelText("Build pipeline"));

    const paper = screen.getByText("Build pipeline").closest(".MuiPopover-paper");
    const popover = within(paper);

    PIPELINE_PHASES.forEach((phase) => {
      expect(popover.getByText(phase.label)).toBeInTheDocument();
    });
    BUILD_PIPELINE.forEach((step) => {
      expect(popover.getByText(step.label)).toBeInTheDocument();
    });

    // The failed row auto-expands with its title/detail and a working Retry.
    expect(popover.getByText("Docker build failed")).toBeInTheDocument();
    expect(popover.getByText("The build step could not finish")).toBeInTheDocument();
    await userEvent.click(popover.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renames the environment in place, committing on Enter", async () => {
    const props = renderHeader(pipelineStatus([], false), { name: "Support" });

    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    const field = screen.getByRole("textbox");
    await userEvent.clear(field);
    await userEvent.type(field, "Support v2{Enter}");

    expect(props.onRename).toHaveBeenCalledTimes(1);
    expect(props.onRename).toHaveBeenCalledWith("Support v2");
  });
});
