// The Experiments list tells a person whose run can't be stopped on its own
// to "Open the experiment to stop all of its runs." That is true: on a
// server without run control, the experiment page's Stop names every active
// run, the server refuses that with 404 run_control_not_enabled, and the
// page then sends the stop with no body, which ends every run of the
// experiment (model_hub/services/experiment_legacy_stop.py cancels all of
// its workflows, its reruns included). Two runs are going here. The request
// functions are a stand-in server that records each request as the API
// client sends it.
/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  resetRunControlForTest,
  runControlState,
} from "src/sections/common/ExperimentRunControl/runControlAvailability";
import ExperimentRow from "../ExperimentRow";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const RUN5 = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e05";
const RUN6 = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e06";

const server = vi.hoisted(() => ({
  requests: [],
  detail: null,
  listeners: new Set(),
  toasts: [],
  emit() {
    this.listeners.forEach((listener) => listener());
  },
  notEnabled() {
    return {
      status: false,
      type: "not_found",
      code: "run_control_not_enabled",
      detail: "Run control is not available.",
      message: "Run control is not available.",
      statusCode: 404,
    };
  },
}));

vi.mock("src/api/develop/experiment-run-actions", async (importOriginal) => ({
  ...(await importOriginal()),
  stopRuns: async (experimentId, runIds) => {
    const body = runIds === undefined ? {} : { run_ids: runIds };
    server.requests.push({
      method: "post",
      url: `/model-hub/experiments/v2/${experimentId}/stop/`,
      body,
    });
    if (body.run_ids) throw server.notEnabled();
    // The stop with no body ends every run of the experiment.
    server.detail = {
      ...server.detail,
      status: "Cancelled",
      runs: server.detail.runs.map((r) => ({ ...r, status: "Cancelled" })),
    };
    server.emit();
    return {
      status: 200,
      data: {
        status: true,
        result: {
          message: "Experiment stopped.",
          experiment_id: experimentId,
          workflows_cancelled: { main: false, reruns: 2 },
          rows_in_flight: 0,
        },
      },
    };
  },
  resumeRuns: async (experimentId, runIds) => {
    server.requests.push({
      method: "post",
      url: `/model-hub/experiments/v2/${experimentId}/resume/`,
      body: { run_ids: runIds },
    });
    throw server.notEnabled();
  },
}));
vi.mock("src/api/develop/runs", async () => {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (listener) => {
    server.listeners.add(listener);
    return () => server.listeners.delete(listener);
  };
  return {
    useExperimentDetail: () => ({
      data: useSyncExternalStore(subscribe, () => server.detail),
    }),
    useDatasetRuns: () => ({ data: { results: [], pinned_run_id: null } }),
    usePinBaseline: () => ({ mutate: () => {} }),
    useUnpinBaseline: () => ({ mutate: () => {} }),
  };
});
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "ds", name: "Tickets" }],
  }),
}));
vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useMatrix: () => ({ data: { rows: [] } }),
  useRunConfigs: () => ({ data: { results: [] } }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: { view: true, download: true, edit: true, run: true, manage: false },
    reason: () => null,
    ask: [],
    isLoading: false,
    view: null,
  }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetKind: () => ({ kind: "rows" }),
}));
vi.mock("src/sections/common/ux/toast", async (importOriginal) => {
  const notify = (message, options = {}) => {
    server.toasts.push({ message, options });
    return { undone: Promise.resolve(false) };
  };
  return { ...(await importOriginal()), default: notify, notify };
});
vi.mock("../iterate/IterateButton", () => ({
  default: () => null,
  iterateDoorHref: () => "/x?iterate=1",
}));
vi.mock("src/sections/common/Runs/RunPickerPopover", () => ({
  default: () => null,
}));
vi.mock("../DevelopExperimentSelect", () => ({
  default: () => <span>Ticket triage</span>,
  experimentStatusOf: () => null,
}));
vi.mock("../results/RunDetailsPopover", () => ({ default: () => null }));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));

const run = (run_id, label) => ({
  run_id,
  experiment_id: EXP,
  experiment_ordinal: 1,
  label,
  name: "Prompt",
  status: "Running",
  progress: { total: 10, done: 4, errors: 0 },
});

function openPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter
        initialEntries={[
          `/dashboard/develop/experiment/${EXP}/data?datasetId=ds`,
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

const stopUrl = `/model-hub/experiments/v2/${EXP}/stop/`;

beforeEach(() => {
  window.innerWidth = 1600;
  resetRunControlForTest();
  server.requests.length = 0;
  server.toasts.length = 0;
  server.listeners.clear();
  server.detail = {
    id: EXP,
    name: "Ticket triage",
    dataset_id: "ds",
    status: "Running",
    runs: [run(RUN5, "#5"), run(RUN6, "#6")],
    runs_total: 2,
    results_availability: { reason: null },
  };
});

describe("the experiment page's Stop without run control", () => {
  it("names both running runs, then sends the stop with no body, and every run reads stopped", async () => {
    openPage();
    fireEvent.click(screen.getByTestId("exp-stop"));
    const dialog = await screen.findByRole("dialog", {
      name: "Stop #5, #6?",
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Stop run" }));

    await waitFor(() =>
      expect(server.requests).toEqual([
        { method: "post", url: stopUrl, body: { run_ids: [RUN5, RUN6] } },
        { method: "post", url: stopUrl, body: {} },
      ]),
    );
    expect(runControlState()).toBe("off");
    expect(server.detail.runs.map((r) => r.status)).toEqual([
      "Cancelled",
      "Cancelled",
    ]);
    const said = server.toasts.filter((t) => t.options.key === "exp-stop");
    expect(said.map((t) => t.message)).toEqual([
      "Stopped #5, #6 · 8 of 20 rows kept",
    ]);
    // The experiment was ended whole: nothing to resume.
    expect(said[0].options.undo).toBeUndefined();
    await waitFor(() => expect(screen.queryByTestId("exp-stop")).toBeNull());
  });
});
