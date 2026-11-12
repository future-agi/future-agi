// Duplicate & edit flashed "Experiment unavailable" for 3-4 s.
// A launch build refuses the supplementary run list at once (a local 404,
// absentPathGuard.absentError), and the gate judged that refusal before the
// experiment itself had loaded. It now waits for the experiment.
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ExperimentSourceGate from "../ExperimentSourceGate";

vi.mock("../RunExperimentFormSkeleton", () => ({
  default: () => <span>Loading experiment</span>,
}));
afterEach(cleanup);

// What useRunConfigs returns in a launch build (r2.js, absentError).
const refusedConfigs = () => ({
  isError: true,
  error: { statusCode: 404, response: { status: 404 } },
  refetch: vi.fn(),
});
const loading = { isPending: true, isLoading: true, data: undefined };
// ExperimentDetailV2: the saved configurations are complete on their own.
const saved = {
  id: "88888888-8888-4888-8888-888888888888",
  name: "Model flows",
  experiment_type: "llm",
  prompt_configs: [
    { id: "11111111-1111-4111-8111-111111111111", model: "liquid/lfm-3-1.7b" },
  ],
  agent_configs: [],
  user_eval_metrics: [],
};
const form = <input aria-label="Duplicate form" />;

describe("Duplicate & edit waits for the experiment", () => {
  it("shows no unavailable notice while the experiment is still loading", () => {
    render(
      <ExperimentSourceGate detail={loading} configs={refusedConfigs()}>
        {form}
      </ExperimentSourceGate>,
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Experiment unavailable"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Loading experiment")).toBeInTheDocument();
    expect(screen.queryByLabelText("Duplicate form")).not.toBeInTheDocument();
  });

  it("opens the copy once the experiment arrives with saved configurations", () => {
    const configs = refusedConfigs();
    const view = render(
      <ExperimentSourceGate detail={loading} configs={configs}>
        {form}
      </ExperimentSourceGate>,
    );
    view.rerender(
      <ExperimentSourceGate detail={{ data: saved }} configs={configs}>
        {form}
      </ExperimentSourceGate>,
    );
    expect(screen.getByLabelText("Duplicate form")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("still says unavailable when the loaded experiment needs the refused list", () => {
    const needsList = {
      ...saved,
      runs: [{ run_id: "run-1", target_type: "prompt" }],
    };
    render(
      <ExperimentSourceGate
        detail={{ data: needsList }}
        configs={refusedConfigs()}
      >
        {form}
      </ExperimentSourceGate>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Experiment unavailable",
    );
    expect(screen.queryByLabelText("Duplicate form")).not.toBeInTheDocument();
  });
});
