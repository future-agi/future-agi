import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import RunChip from "../Runs/RunChip";
import RunStatusChip, { StatusChip } from "../Runs/RunStatusChip";
import BaselinePicker from "../Runs/BaselinePicker";
import RunPickerPopover from "../Runs/RunPickerPopover";
import { runColorMap } from "../Runs/runColors";
import DeltaBadge from "../Metrics/DeltaBadge";
import DirectionIcon from "../Metrics/DirectionIcon";
import MovementButtons from "../Metrics/MovementButtons";
import ScoreCell from "../Metrics/ScoreCell";
import MetricHeader from "../Metrics/MetricHeader";
import VersionChip from "../Datasets/VersionChip";
import VersionTagChip from "../Datasets/VersionTagChip";
import SplitChips from "../Datasets/SplitChips";

const RUNS = [
  {
    run_id: "RUN_3A",
    label: "#3A",
    name: "Support Agent v2",
    status: "Completed",
    progress: { done: 238, total: 238, errors: 0 },
    dataset_version: {
      id: "VER_5",
      number: 5,
      tag: "golden-v3",
      is_latest: false,
    },
    target_type: "agent",
    target: { graph_id: "GRAPH_SUPPORT", graph_name: "Support Agent" },
    created_at: "2026-10-10T09:00:00Z",
  },
  {
    run_id: "RUN_4A",
    label: "#4A",
    name: "Support Agent v3",
    status: "Completed",
    progress: { done: 248, total: 248, errors: 2 },
    first_error: "lookup_invoice timeout after 30s",
    dataset_version: { id: "VER_6", number: 6, tag: null, is_latest: true },
    target_type: "agent",
    target: { graph_id: "GRAPH_SUPPORT", graph_name: "Support Agent" },
    created_at: "2026-10-12T09:00:00Z",
  },
  {
    run_id: "RUN_1A",
    label: "#1A",
    name: "Support Reply V3 × gpt-4o-mini",
    status: "Completed",
    is_dataset_baseline: true,
    progress: { done: 238, total: 238, errors: 0 },
    dataset_version: {
      id: "VER_5",
      number: 5,
      tag: "golden-v3",
      is_latest: false,
    },
    target_type: "prompt",
    target: { prompt_id: "PROMPT_REPLY", prompt_name: "Support Reply" },
    created_at: "2026-10-08T09:00:00Z",
  },
];

vi.mock("src/api/develop/runs", () => ({
  useDatasetRuns: () => ({
    data: { results: RUNS, baseline: { run_id: "RUN_1A", label: "#1A" } },
    isLoading: false,
    isError: false,
    refetch: () => {},
  }),
  usePinBaseline: () => ({ mutate: vi.fn(), isPending: false }),
  useUnpinBaseline: () => ({ mutate: vi.fn(), isPending: false }),
}));

const correctness = {
  key: "eval:correctness",
  name: "Correctness",
  output_type: "pass_fail",
  higher_is_better: true,
};
const taskCompletion = {
  ...correctness,
  key: "eval:task_completion",
  name: "Task completion",
};
const groundedness = {
  key: "eval:groundedness",
  name: "Groundedness",
  output_type: "score",
  higher_is_better: true,
};

describe("Runs primitives", () => {
  it("RunChip shows label, name, baseline badge and error count", () => {
    const colors = runColorMap(["RUN_3A", "RUN_4A"], "RUN_3A");
    render(<RunChip run={RUNS[1]} color={colors.RUN_4A} isBaseline />);
    expect(screen.getByText("#4A")).toBeInTheDocument();
    expect(screen.getByText("Support Agent v3")).toBeInTheDocument();
    expect(screen.getByText("Baseline")).toBeInTheDocument();
    expect(screen.getByText("2 err")).toBeInTheDocument();
  });

  it("RunChip shows progress while running and opens its menu", () => {
    const onClick = vi.fn();
    render(
      <RunChip
        run={{
          run_id: "RUN_7A",
          label: "#7A",
          status: "Running",
          progress: { done: 10, total: 48, errors: 0 },
        }}
        menu={[{ label: "Use as baseline run", onClick }]}
      />,
    );
    expect(screen.getByText("10/48")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    fireEvent.click(screen.getByText("Use as baseline run"));
    expect(onClick).toHaveBeenCalled();
  });

  it("StatusChip relabels Cancelled and keeps unknown text", () => {
    render(
      <>
        <StatusChip status="Cancelled" />
        <StatusChip status="Paused" />
      </>,
    );
    expect(screen.getByText("Stopped")).toBeInTheDocument();
    expect(screen.getByText("Paused")).toBeInTheDocument();
  });

  it("RunStatusChip shows progress and the actions that apply", () => {
    const onStop = vi.fn();
    render(
      <RunStatusChip
        run={{
          status: "Running",
          progress: { done: 142, total: 248, errors: 0 },
        }}
        showProgress
        actions={{ onStop, onResume: vi.fn(), onRetry: vi.fn() }}
      />,
    );
    expect(screen.getByText("142/248")).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button"));
    expect(onStop).toHaveBeenCalled();
  });

  it("BaselinePicker groups runs and reports the choice", () => {
    const onChange = vi.fn();
    render(
      <BaselinePicker
        datasetId="DS"
        value="RUN_3A"
        pinnedRunId="RUN_1A"
        runsInView={[RUNS[0], RUNS[1]]}
        allowNone
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^Baseline / }));
    expect(screen.getByText("Pinned baseline")).toBeInTheDocument();
    expect(screen.getByText("In this view")).toBeInTheDocument();
    expect(screen.getByText("None")).toBeInTheDocument();
    expect(screen.getByText("Pin #3A as dataset baseline")).toBeInTheDocument();
    fireEvent.click(screen.getAllByText("#4A")[0]);
    expect(onChange).toHaveBeenCalledWith("RUN_4A", { scope: "view" });
  });

  it("RunPickerPopover groups same target first and returns picks", () => {
    const onPick = vi.fn();
    render(
      <RunPickerPopover
        datasetId="DS"
        excludeIds={["RUN_3A"]}
        groupBySameTarget
        onPick={onPick}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Add runs" }));
    expect(screen.getByText("Same target")).toBeInTheDocument();
    expect(screen.getByText("In this dataset")).toBeInTheDocument();
    fireEvent.click(screen.getByText("#4A"));
    fireEvent.click(screen.getByRole("button", { name: "Add run" }));
    expect(onPick).toHaveBeenCalledWith(["RUN_4A"]);
  });
});

describe("Metrics primitives", () => {
  it("DeltaBadge colours by direction and greys noise without a glyph", () => {
    const { container } = render(
      <>
        <DeltaBadge
          def={correctness}
          delta={0.071}
          ci95={0.04}
          significant
          n={238}
          vsLabel="#1A"
        />
        <DeltaBadge
          def={taskCompletion}
          delta={0.025}
          ci95={0.034}
          significant={false}
          n={236}
          vsLabel="#3A"
        />
        <DeltaBadge def={correctness} delta={0.05} significant n={20} />
      </>,
    );
    expect(screen.getByText("+7.1 pts")).toBeInTheDocument();
    expect(screen.getByText("+2.5 pts")).toBeInTheDocument();
    const tones = [...container.querySelectorAll("[data-tone]")].map(
      (n) => n.dataset.tone,
    );
    expect(tones).toEqual(["good", "noise", "good"]);
    expect(screen.queryByText(/~/)).toBeNull();
    expect(screen.getByText("n=20")).toBeInTheDocument();
  });

  it("MovementButtons toggles a direction", () => {
    const onSelect = vi.fn();
    render(
      <MovementButtons
        improved={14}
        regressed={7}
        unchanged={215}
        notComparable={12}
        active="regressed"
        onSelect={onSelect}
      />,
    );
    expect(screen.getByText("↑14")).toBeInTheDocument();
    const down = screen.getByRole("button", { name: /7 regressed, filter on/ });
    expect(down).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: /14 improved/ }));
    expect(onSelect).toHaveBeenCalledWith("improved");
  });

  it("ScoreCell renders value, error and not-scored states", () => {
    const onFilter = vi.fn();
    render(
      <>
        <ScoreCell
          def={correctness}
          value={0}
          movement="regressed"
          delta={-1}
          flaky
          reason="Says 3–5 days"
          showReason
          onFilter={onFilter}
        />
        <ScoreCell
          def={groundedness}
          value={0.41}
          delta={-0.2}
          movement="regressed"
        />
        <ScoreCell def={groundedness} error="RateLimitError" />
        <ScoreCell def={groundedness} value={null} />
      </>,
    );
    expect(screen.getByText("Fail")).toBeInTheDocument();
    expect(screen.getByText("↓")).toBeInTheDocument();
    expect(screen.getByText("Says 3–5 days")).toBeInTheDocument();
    expect(screen.getByText("0.41")).toBeInTheDocument();
    expect(screen.getByText("−0.20")).toBeInTheDocument();
    expect(screen.getByText("Error · RateLimitError")).toBeInTheDocument();
    expect(screen.getByText("Not scored")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Filter by this" }));
    expect(onFilter).toHaveBeenCalled();
  });

  it("MetricHeader shows run, aggregate, delta and movement; baseline shows none", () => {
    const onMovement = vi.fn();
    const { rerender } = render(
      <MetricHeader
        def={taskCompletion}
        run={RUNS[1]}
        aggregate={0.809}
        delta={{
          delta: 0.025,
          ci95: 0.034,
          significant: false,
          n: 236,
          vsLabel: "#3A",
        }}
        movement={{
          improved: 14,
          regressed: 7,
          unchanged: 215,
          not_comparable: 12,
        }}
        onMovement={onMovement}
        menuItems={[{ label: "Hide column", onClick: vi.fn() }]}
      />,
    );
    expect(screen.getByText("#4A")).toBeInTheDocument();
    expect(screen.getByText("80.9%")).toBeInTheDocument();
    expect(screen.getByText("+2.5 pts")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /7 regressed/ }));
    expect(onMovement).toHaveBeenCalledWith("regressed");
    rerender(
      <MetricHeader
        def={taskCompletion}
        run={RUNS[0]}
        aggregate={0.78}
        isBaseline
      />,
    );
    expect(screen.getByText("78.0%")).toBeInTheDocument();
    expect(screen.queryByText(/pts/)).toBeNull();
    expect(screen.getByText("Baseline")).toBeInTheDocument();
  });

  it("DirectionIcon renders only with a direction", () => {
    const { container, rerender } = render(<DirectionIcon higherIsBetter />);
    expect(
      container.querySelector("[aria-label='Higher is better']"),
    ).toBeTruthy();
    rerender(<DirectionIcon higherIsBetter={null} />);
    expect(container.querySelector("[aria-label]")).toBeNull();
  });
});

describe("Datasets primitives", () => {
  it("VersionChip reads `v6 · latest · tag` and flags old versions", () => {
    const onClick = vi.fn();
    render(
      <>
        <VersionChip
          version={{ number: 7, tag: "golden-v4", is_latest: true }}
          onClick={onClick}
        />
        <VersionChip
          version={{ number: 5, tag: "golden-v3", is_latest: false }}
          staleInfo={{ latest: 6, added: 10, modified: 2 }}
        />
      </>,
    );
    expect(screen.getByText("v7")).toBeInTheDocument();
    expect(screen.getByText("latest")).toBeInTheDocument();
    expect(screen.getByText("golden-v4")).toBeInTheDocument();
    expect(screen.getByLabelText("Not the latest version")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dataset version v7" }));
    expect(onClick).toHaveBeenCalled();
  });

  it("VersionTagChip renders the tag", () => {
    render(<VersionTagChip name="golden-v3" size="medium" />);
    expect(screen.getByText("golden-v3")).toBeInTheDocument();
  });

  it("SplitChips shows max chips and +N", () => {
    const onClick = vi.fn();
    const { container } = render(
      <SplitChips
        splits={[
          { id: "S1", name: "smoke", color: "info" },
          { id: "S2", name: "hard", color: "error" },
          { id: "S3", name: "test", color: "secondary" },
        ]}
        onClick={onClick}
      />,
    );
    const scope = within(container);
    expect(scope.getByText("smoke")).toBeInTheDocument();
    expect(scope.getByText("hard")).toBeInTheDocument();
    expect(scope.queryByText("test")).toBeNull();
    expect(scope.getByText("+1")).toBeInTheDocument();
    fireEvent.click(scope.getByText("hard"));
    expect(onClick).toHaveBeenCalledWith(expect.objectContaining({ id: "S2" }));
  });
});
