import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import ExperimentTabEmptyState from "../ExperimentTabEmptyState";

// The Experiments tab's empty state, with and without an SDK run's panel
// above it (the page opened at a run's app_url, ?tab=experiments&run=<id>).
describe("ExperimentTabEmptyState", () => {
  it("offers the first experiment when no SDK run is shown", () => {
    const onRunExperiment = vi.fn();
    render(<ExperimentTabEmptyState onRunExperiment={onRunExperiment} />);

    expect(
      screen.getByText("Ready to run your first experiment"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run Experiment" }));
    expect(onRunExperiment).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByTestId("experiment-tab-empty-with-sdk-run"),
    ).not.toBeInTheDocument();
  });

  it("does not say 'run your first experiment' under an SDK run's panel", () => {
    render(<ExperimentTabEmptyState sdkRunShown onRunExperiment={vi.fn()} />);

    expect(
      screen.getByTestId("experiment-tab-empty-with-sdk-run"),
    ).toHaveTextContent(
      "The SDK run above is shown by its run id. This table lists the experiments run in the app; this dataset has none yet.",
    );
    expect(
      screen.queryByText("Ready to run your first experiment"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Run Experiment" }),
    ).not.toBeInTheDocument();
  });
});
