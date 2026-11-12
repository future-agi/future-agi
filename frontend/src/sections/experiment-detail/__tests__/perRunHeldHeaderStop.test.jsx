// On a server without run control, a run's own Stop (the results run chip)
// is held: it says why and never stops the whole experiment. The experiment
// page's Stop is then the one Stop that ends the spending, so from that same
// state it must still stop the experiment, with the empty-body stop only.
// Both parts render together as on the experiment page. The request
// functions are replaced by a stand-in server that records each request as
// the API client would send it.
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
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { useExperimentDetail } from "src/api/develop/runs";
import { resetRunControlForTest } from "src/sections/common/ExperimentRunControl/runControlAvailability";
import ExperimentRow from "../ExperimentRow";
import RunBar from "../results/RunBar";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const RUN = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e4f";
const STOP_HELD = `${NOT_IN_RELEASE}. Stop the whole experiment from its page.`;

const server = vi.hoisted(() => ({
  runControl: false,
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
  // Every run the stand-in stops reads Stopped, rows left, on the next poll.
  markStopped() {
    this.detail = {
      ...this.detail,
      status: "Cancelled",
      runs: this.detail.runs.map((r) => ({ ...r, status: "Cancelled" })),
    };
    this.emit();
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
    if (body.run_ids && !server.runControl) throw server.notEnabled();
    server.markStopped();
    return {
      status: body.run_ids ? 202 : 200,
      data: {
        status: true,
        result: {
          experiment_id: experimentId,
          runs: (runIds ?? []).map((id) => ({
            run_id: id,
            done: 4,
            total: 10,
          })),
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
    if (!server.runControl) throw server.notEnabled();
    return { status: 202, data: { status: true, result: { runs: [] } } };
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

const runningExperiment = () => ({
  id: EXP,
  name: "Ticket triage",
  dataset_id: "ds",
  status: "Running",
  runs: [
    {
      run_id: RUN,
      experiment_id: EXP,
      experiment_ordinal: 1,
      label: "#1A",
      name: "Prompt",
      status: "Running",
      progress: { total: 10, done: 4, errors: 0 },
    },
  ],
  runs_total: 1,
  results_availability: { reason: null },
});

// The experiment page: its header, and the results run chips under it.
function Page() {
  const { data } = useExperimentDetail(EXP);
  return (
    <>
      <ExperimentRow />
      <RunBar
        datasetId="ds"
        mode="experiment"
        runs={data.runs}
        colors={{}}
        onBaselineChange={() => {}}
      />
    </>
  );
}

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
            element={<Page />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function chipItem(name) {
  fireEvent.click(screen.getByLabelText("#1A menu"));
  return screen.findByRole("menuitem", { name });
}

async function confirmHeaderStop() {
  fireEvent.click(screen.getByTestId("exp-stop"));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Stop run" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
}

const stopUrl = `/model-hub/experiments/v2/${EXP}/stop/`;
const toastsWith = (key) => server.toasts.filter((t) => t.options.key === key);

beforeEach(() => {
  window.innerWidth = 1600;
  resetRunControlForTest();
  server.requests.length = 0;
  server.toasts.length = 0;
  server.listeners.clear();
  server.detail = runningExperiment();
});

describe("the experiment page's Stop after a run's own Stop was refused", () => {
  it("run control off: the run chip says why, then the page's Stop stops the experiment", async () => {
    server.runControl = false;
    openPage();
    fireEvent.click(await chipItem(/^Stop #1A/));
    await waitFor(() =>
      expect(toastsWith(`run-action-${RUN}`)).toHaveLength(1),
    );
    const said = toastsWith(`run-action-${RUN}`)[0];
    expect(said.message).toBe(STOP_HELD);
    expect(said.options.variant).toBe("info");
    // Refused, with no effect: the experiment still runs.
    expect(server.requests).toEqual([
      { method: "post", url: stopUrl, body: { run_ids: [RUN] } },
    ]);
    expect(screen.getByTestId("exp-stop")).toBeInTheDocument();

    // The page knows: its Stop sends only the empty-body stop, and it stops.
    await confirmHeaderStop();
    await waitFor(() =>
      expect(server.requests).toEqual([
        { method: "post", url: stopUrl, body: { run_ids: [RUN] } },
        { method: "post", url: stopUrl, body: {} },
      ]),
    );
    const stopped = toastsWith("exp-stop").pop();
    expect(stopped.message).toBe("Stopped #1A · 4 of 10 rows kept");
    expect(stopped.options.undo).toBeUndefined();
    await waitFor(() => expect(screen.queryByTestId("exp-stop")).toBeNull());

    // The chip's Resume is held too, with the shared line.
    const resume = await chipItem(/^Resume #1A/);
    expect(resume).toHaveAttribute("aria-disabled", "true");
    expect(within(resume).getByText(NOT_IN_RELEASE)).toBeInTheDocument();
    expect(server.requests.filter((r) => r.url.endsWith("/resume/"))).toEqual(
      [],
    );
  });

  it("run control on: the run chip and the page name the runs, as before", async () => {
    server.runControl = true;
    openPage();
    fireEvent.click(await chipItem(/^Stop #1A/));
    await waitFor(() =>
      expect(server.requests).toEqual([
        { method: "post", url: stopUrl, body: { run_ids: [RUN] } },
      ]),
    );
    expect(toastsWith(`run-action-${RUN}`)[0].options.variant).toBe("success");
  });
});
