// dataset integration FE-0 (owner Q12) on the experiment header: the Rerun menu reads
// each run's kind from the dataset's run configs (the experiment's run list
// has none). TS #3 (code and harness runs only) offered `Test a fix on #3A`
// (a dock that never opens on a code run), `All tasks, all runs · + your
// runner` and `Rerun with changes… Start from #3F`; those doors are absent.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = { runs: [], configs: [] };
const progress = { total: 2, done: 2, errors: 0 };

vi.mock("src/api/develop/runs", () => ({
  useExperimentDetail: () => ({
    data: {
      id: "e3",
      ordinal: 3,
      name: "Frameworks",
      dataset_id: "ds",
      runs: state.runs,
    },
  }),
  useDatasetRuns: () => ({ data: { results: state.configs } }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: [{ datasetId: "ds", name: "Tasks" }] }),
}));
vi.mock("src/api/develop/r2", () => ({
  useMatrix: () => ({ data: { rows: [] } }),
  useRunConfigs: () => ({ data: { results: state.configs } }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "task_suite" }),
}));
vi.mock("../../iterate/IterateButton", () => ({
  default: () => null,
  iterateDoorHref: () => "/x?iterate=1",
}));
vi.mock("src/sections/common/Runs/RunPickerPopover", () => ({
  default: () => null,
}));
vi.mock("../../DevelopExperimentSelect", () => ({
  default: () => <span>#3 Frameworks</span>,
  experimentStatusOf: () => null,
}));

import ExperimentRow from "../../ExperimentRow";

const run = (id, label, name) => ({ run_id: id, label, name, progress });
const config = (id, label, kind) => ({
  run_id: id,
  label,
  config: { kind },
});

function renderRow() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter
        initialEntries={["/dashboard/develop/experiment/e3/data?datasetId=ds"]}
      >
        <Routes>
          <Route
            path="/dashboard/develop/experiment/:experimentId/data"
            element={<ExperimentRow />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Experiment header Rerun menu after FE-0", () => {
  beforeEach(() => {
    window.innerWidth = 1600;
  });

  it("has no Rerun at all when every run is a code or harness run", () => {
    state.runs = [run("a", "#3A", "LangGraph"), run("f", "#3F", "OpenHands")];
    state.configs = [config("a", "#3A", "code"), config("f", "#3F", "harness")];
    renderRow();
    expect(screen.queryByTestId("exp-rerun")).toBeNull();
    expect(screen.queryByTestId("exp-rerun-anchor")).toBeNull();
  });

  it("offers only the runs Future AGI can start again in a mixed experiment", () => {
    state.runs = [
      run("p", "#2A", "Prompt"),
      run("c", "#2B", "Your code"),
      run("g", "#2C", "Agent"),
    ];
    state.configs = [
      config("p", "#2A", "prompt"),
      config("c", "#2B", "code"),
      config("g", "#2C", "agent"),
    ];
    renderRow();
    fireEvent.click(screen.getByTestId("exp-rerun"));
    const menu = screen.getByTestId("exp-rerun-menu");
    expect(screen.getByTestId("exp-rerun-all").textContent).toContain(
      "All tasks, #2A, #2C",
    );
    expect(menu.textContent).not.toContain("all runs");
    expect(screen.getByTestId("exp-rerun-changes").textContent).toContain(
      "Start from #2C Agent",
    );
    expect(menu.textContent).not.toContain("#2B");
    // Test a fix targets a run it applies to, never the code run.
    expect(screen.getByTestId("exp-rerun-test-fix").textContent).not.toContain(
      "#2B",
    );
  });
});
