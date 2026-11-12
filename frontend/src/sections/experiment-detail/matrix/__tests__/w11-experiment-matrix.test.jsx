/* eslint-disable react/prop-types */
// The experiment's Matrix tab read every run of the experiment. After a run
// with other evaluators joined, the read was refused as a mixed comparison.
// The tab now reads the newest group of runs that line up (the rule of the
// experiment header and the Data tab), and its Run history line names the
// runs it shows; picking an earlier group shows that one.
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ matrixProps: [] }));

vi.mock("src/api/develop/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useExperimentDetail: () => ({
    data: {
      dataset_id: "ds-1",
      // Newest first: #3 ran with another evaluator; #2 and #1 line up.
      runs: [
        { run_id: "run-3", label: "#3", comparison_group: "plan-new" },
        { run_id: "run-2", label: "#2", comparison_group: "plan-old" },
        { run_id: "run-1", label: "#1", comparison_group: "plan-old" },
      ],
    },
  }),
}));
vi.mock("src/sections/experiment-detail/matrix/MatrixView", () => ({
  default: (props) => {
    h.matrixProps.push(props);
    return (
      <div data-testid="matrix-runs">
        {`${props.runIds.join(",")}|${props.baselineId}`}
      </div>
    );
  },
}));

import ExperimentMatrix from "src/pages/dashboard/Develop/ExperimentMatrix";

const renderAt = (search = "") =>
  render(
    <HelmetProvider>
      <MemoryRouter
        initialEntries={[`/dashboard/develop/experiment/exp-1/matrix${search}`]}
      >
        <Routes>
          <Route
            path="/dashboard/develop/experiment/:experimentId/matrix"
            element={<ExperimentMatrix />}
          />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );

afterEach(() => {
  cleanup();
  h.matrixProps = [];
});

describe("the experiment's Matrix tab", () => {
  it("reads only the newest runs that line up, and says which", () => {
    renderAt();
    expect(screen.getByTestId("matrix-runs")).toHaveTextContent(
      /^run-3\|run-3$/,
    );
    const picker = screen.getByRole("combobox", { name: "Run history" });
    expect(picker).toHaveDisplayValue("Run #3");
    expect(
      screen.getByRole("option", { name: "Runs #2, #1" }),
    ).toBeInTheDocument();
  });

  it("shows the earlier group when it is picked, with a baseline from it", () => {
    renderAt("?baseline=run-3");
    fireEvent.change(screen.getByRole("combobox", { name: "Run history" }), {
      target: { value: "run-2" },
    });
    expect(screen.getByTestId("matrix-runs")).toHaveTextContent(
      /^run-2,run-1\|run-2$/,
    );
    for (const props of h.matrixProps)
      expect(new Set(props.runIds.map((id) => id === "run-3")).size).toBe(1);
  });
});
