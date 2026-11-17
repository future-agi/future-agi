// Experiments tab, grouped by experiment: the group row reads `#4 name`.
// The backend's run list has no experiment number, so the row showed a
// bare `#` before the name. With no number the name alone labels it.
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import ExperimentNameCell from "../ExperimentNameCell";

vi.mock("src/sections/common/Tags/ExperimentTagChips", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/RunSources/SourceLogo", () => ({
  default: () => null,
}));

const DATASET_ID = "7f3c0a11-0000-4000-8000-0000000000f2";

// One run as results_catalog.project_run sends it: the run's own #n, the
// experiment's id and name, no experiment number.
const backendRun = (n, extra = {}) => ({
  run_id: `run-${n}`,
  dataset_id: DATASET_ID,
  experiment_id: "walk-exp",
  experiment_name: "walk-exp-model-flows-1111",
  experiment_description: null,
  ordinal: n,
  label: `#${n}`,
  name: `#${n}`,
  status: "completed",
  task_kind: "prompt",
  kind: "execution",
  listed: true,
  config: { model: { name: "liquid/lfm-3-1.7b", provider: "custom" } },
  ...extra,
});

const renderGroup = (runs) =>
  render(
    <MemoryRouter>
      <ExperimentNameCell
        node={{ group: true, allLeafChildren: runs.map((data) => ({ data })) }}
        context={{
          group: "experiment",
          datasetId: DATASET_ID,
          allRuns: runs,
          q: "",
          latestExperimentId: "walk-exp",
          baselineRun: null,
        }}
      />
    </MemoryRouter>,
  );

afterEach(() => cleanup());

describe("Experiments tab group row number", () => {
  it("labels the group by its name when the run list has no experiment number", () => {
    const { container } = renderGroup([
      backendRun(3),
      backendRun(2),
      backendRun(1),
    ]);

    const name = screen.getByText("walk-exp-model-flows-1111");
    expect(name.closest("a")?.getAttribute("href")).toContain(
      "/dashboard/develop/experiment/walk-exp/",
    );
    expect(screen.getByText("Latest")).toBeTruthy();
    expect(screen.getByText("3 runs")).toBeTruthy();
    expect(screen.queryByText("#")).toBeNull();
    expect(container.textContent).not.toContain("#");
    expect(container.textContent).not.toMatch(/undefined|null|NaN/);
  });

  it("keeps the number when the run carries one", () => {
    renderGroup([backendRun(1, { experiment_ordinal: 4 })]);

    expect(screen.getByText("#4")).toBeTruthy();
    expect(screen.getByText("walk-exp-model-flows-1111")).toBeTruthy();
  });
});
