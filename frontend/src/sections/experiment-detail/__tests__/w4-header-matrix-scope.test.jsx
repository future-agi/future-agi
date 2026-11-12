// W4 (walk 1111, row 9): the experiment page showed a red toast "Runs require
// the same immutable version, trial plan and evaluator bindings; mixed
// comparison is not supported." The header (ExperimentRow) read the results
// matrix over EVERY run of the experiment with no baseline, so the server
// also took the dataset's pinned run. Once an evaluation Re-run added a run
// with other evaluators (the Data tab's "Run history" select), or the pinned
// run came from another plan, that read was refused. The header now reads
// only the newest group the server can line up (the Data tab's group), names
// a baseline from it, and a refusal never toasts.
/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = { runs: [], pinned: null };
const matrixCalls = [];
const progress = { total: 3, done: 3, errors: 0 };

vi.mock("src/api/develop/runs", () => ({
  useExperimentDetail: () => ({
    data: {
      id: "e1",
      ordinal: 1,
      name: "walk-exp-model-flows-1111",
      dataset_id: "ds",
      runs: state.runs,
    },
  }),
  useDatasetRuns: () => ({
    data: { results: [], pinned_run_id: state.pinned },
  }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "ds", name: "walk-model-flows-1111" }],
  }),
}));
vi.mock("src/api/develop/r2", () => ({
  useMatrix: (params, options) => {
    matrixCalls.push({ params, options });
    return { data: { rows: [] } };
  },
  useRunConfigs: () => ({ data: { results: [] } }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "rows" }),
}));
vi.mock("../iterate/IterateButton", () => ({
  default: () => null,
  iterateDoorHref: () => "/x?iterate=1",
}));
vi.mock("src/sections/common/Runs/RunPickerPopover", () => ({
  default: () => null,
}));
vi.mock("../DevelopExperimentSelect", () => ({
  default: () => <span>walk-exp-model-flows-1111</span>,
  experimentStatusOf: () => null,
}));

import ExperimentRow from "../ExperimentRow";

// The catalog lists runs newest first; comparison_group is the server's
// "same version, plan and evaluators" key.
const run = (id, label, group) => ({
  run_id: id,
  label,
  name: "Prompt liquid/lfm-3-1.7b",
  status: "Completed",
  comparison_group: group,
  progress,
});

function renderRow(query = "") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter
        initialEntries={[
          `/dashboard/develop/experiment/e1/data?datasetId=ds${query}`,
        ]}
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

const lastMatrixRead = () => matrixCalls[matrixCalls.length - 1];

describe("Experiment header matrix read", () => {
  beforeEach(() => {
    window.innerWidth = 1600;
    matrixCalls.length = 0;
    state.pinned = null;
  });

  it("never reads runs the server cannot line up in one read", () => {
    state.runs = [
      run("r3", "#3", "rescored"),
      run("r2", "#2", "rescored"),
      run("r1", "#1", "first"),
    ];
    renderRow();
    const { params, options } = lastMatrixRead();
    expect(params.runIds).toEqual(["r3", "r2"]);
    expect(params.baselineId).toBe("r3");
    // A refusal leaves the optional numbers out; no app-wide toast.
    expect(options.meta).toEqual({ errorHandled: true });
    expect(options.enabled).toBe(true);
    matrixCalls.forEach((call) =>
      expect(call.params.runIds).not.toEqual(
        expect.arrayContaining(["r3", "r1"]),
      ),
    );
  });

  it("follows the Data tab's Run history pick", () => {
    state.runs = [
      run("r3", "#3", "rescored"),
      run("r2", "#2", "rescored"),
      run("r1", "#1", "first"),
    ];
    renderRow("&history=r1");
    expect(lastMatrixRead().params.runIds).toEqual(["r1"]);
    expect(lastMatrixRead().params.baselineId).toBe("r1");
  });

  it("keeps the page's baseline when it is in the group", () => {
    state.runs = [
      run("r3", "#3", "rescored"),
      run("r2", "#2", "rescored"),
      run("r1", "#1", "first"),
    ];
    renderRow("&baseline=r2");
    expect(lastMatrixRead().params.runIds).toEqual(["r3", "r2"]);
    expect(lastMatrixRead().params.baselineId).toBe("r2");
  });

  it("names the run as baseline so a pin from another plan is not pulled in", () => {
    state.runs = [run("r1", "#1", "first")];
    state.pinned = "run-of-another-experiment";
    renderRow();
    const { params, options } = lastMatrixRead();
    expect(params.runIds).toEqual(["r1"]);
    expect(params.baselineId).toBe("r1");
    expect(options.meta).toEqual({ errorHandled: true });
  });

  it("reads one group of compatible runs whole, with the pinned run as baseline", () => {
    state.runs = [run("b", "#1B", "same"), run("a", "#1A", "same")];
    state.pinned = "a";
    renderRow();
    expect(lastMatrixRead().params.runIds).toEqual(["b", "a"]);
    expect(lastMatrixRead().params.baselineId).toBe("a");
  });
});
