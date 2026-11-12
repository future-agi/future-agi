// The new experiment page's Stop must stop a running experiment on every
// server, whatever its run-control setting. Stop names the active runs
// (selected-run control). A server without run control answers that request
// 404 "run_control_not_enabled" and changes nothing; the page then stops the
// experiment with the empty-body stop the classic page sends, and Resume
// stays visible but disabled with the shared line. A server with run control
// keeps the run-id Stop and Resume. Both launch-switch states are pinned the
// way the launch-gate tests pin them. The request functions are replaced by a
// stand-in server that records each request as the API client would send it
// (the bodies themselves are pinned in experiment-stop-scope.test.js).
/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const RUN = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e4f";

const server = vi.hoisted(() => ({
  runControl: false,
  requests: [],
  detail: null,
  listeners: new Set(),
  toasts: [],
  emit() {
    this.listeners.forEach((listener) => listener());
  },
  // The API client's error for the server's refusal (body + statusCode).
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
const unreleased = vi.hoisted(() => ({ shown: true }));

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
    return body.run_ids
      ? {
          status: 202,
          data: {
            status: true,
            result: {
              experiment_id: experimentId,
              runs: runIds.map((id) => ({
                run_id: id,
                name: "Prompt",
                status: "STOPPED",
                done: 4,
                total: 10,
              })),
            },
          },
        }
      : {
          status: 200,
          data: {
            status: true,
            result: {
              message: "Experiment stopped.",
              experiment_id: experimentId,
              workflows_cancelled: { main: true, reruns: 0 },
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
    return {
      status: 202,
      data: {
        status: true,
        result: {
          experiment_id: experimentId,
          runs: runIds.map((id) => ({ run_id: id, pending: 6 })),
        },
      },
    };
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
  };
});
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "ds", name: "Tickets" }],
  }),
}));
vi.mock("src/api/develop/r2", () => ({
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
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
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
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));
// The show-unreleased switch, read by the page selector, pinned per test.
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});

const run = (status) => ({
  run_id: RUN,
  experiment_id: EXP,
  label: "#1A",
  name: "Prompt",
  status,
  progress: { total: 10, done: 4, errors: 0 },
});

const newExperiment = (status = "Running") => ({
  id: EXP,
  name: "Ticket triage",
  dataset_id: "ds",
  status,
  runs: [run(status)],
  runs_total: 1,
  results_availability: { reason: null },
});

// An experiment made before runs were recorded: no run rows at all.
const olderExperiment = (status = "Running") => ({
  id: EXP,
  name: "Ticket triage (before runs)",
  dataset_id: "ds",
  status,
  runs: [],
  runs_total: 0,
  results_availability: { reason: "native_identity_not_recorded" },
});

// A fresh module graph per test: what the page learned from the server
// lives only for one page session.
async function openPage({ shown }) {
  vi.resetModules();
  unreleased.shown = shown;
  const launch = await import("src/launch/launchSwitch");
  launch.setLaunchOverrideForTest(
    shown
      ? { launch: true, experimentsBasic: true }
      : { launch: true, showUnreleased: false, experimentsBasic: true },
  );
  const { useExperimentSurface } = await import(
    "src/sections/experiment-detail-classic/experimentsBasic"
  );
  const surface = renderHook(() => useExperimentSurface()).result.current;
  const { default: ExperimentRow } = await import("../ExperimentRow");
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
  return { surface };
}

// Opens Stop's confirm, confirms it, waits for it to close; returns its text.
async function confirmStop(confirmLabel = "Stop run") {
  fireEvent.click(screen.getByTestId("exp-stop"));
  const dialog = await screen.findByRole("dialog");
  const body = dialog.textContent;
  fireEvent.click(within(dialog).getByRole("button", { name: confirmLabel }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  return body;
}

const stopUrl = `/model-hub/experiments/v2/${EXP}/stop/`;
const resumeUrl = `/model-hub/experiments/v2/${EXP}/resume/`;
const gatedRequests = () =>
  server.requests.filter(
    (r) => r.url.endsWith("/resume/") || r.url.endsWith("/run-control/"),
  );
const lastToast = (key) =>
  [...server.toasts].reverse().find((t) => t.options.key === key);

// Each test loads the page afresh; load it once first so the transforms are
// cached outside the per-test time limit.
const SLOW = 60000;
beforeAll(async () => {
  await import("../ExperimentRow");
}, 120000);
beforeEach(() => {
  window.innerWidth = 1600;
  server.requests.length = 0;
  server.listeners.clear();
  server.toasts.length = 0;
});
afterEach(async () => {
  const launch = await import("src/launch/launchSwitch");
  launch.setLaunchOverrideForTest(null);
});

describe.each([
  { state: "switch on (shipped build)", shown: true, surface: "dsx" },
  { state: "switch off", shown: false, surface: "classic" },
])("Stop on the new experiment page, $state", ({ shown, surface }) => {
  it(
    "selects the page this switch state opens",
    async () => {
      server.runControl = false;
      server.detail = newExperiment();
      const opened = await openPage({ shown });
      expect(opened.surface).toBe(surface);
    },
    SLOW,
  );

  it(
    "run control off: Stop cancels with the empty-body stop and the experiment reads stopped",
    async () => {
      server.runControl = false;
      server.detail = newExperiment();
      await openPage({ shown });
      const body = await confirmStop();
      await waitFor(() =>
        expect(server.requests).toEqual([
          { method: "post", url: stopUrl, body: { run_ids: [RUN] } },
          { method: "post", url: stopUrl, body: {} },
        ]),
      );
      const toast = lastToast("exp-stop");
      expect(toast.message).toBe("Stopped #1A · 4 of 10 rows kept");
      expect(toast.options.variant).toBeUndefined();
      // A cancelled experiment has nothing to resume: no Undo.
      expect(toast.options.undo).toBeUndefined();
      await waitFor(() => expect(screen.queryByTestId("exp-stop")).toBeNull());
      expect(gatedRequests()).toEqual([]);
      // The confirm never promised a resume the server had not offered.
      expect(body).not.toMatch(/resume/i);
    },
    SLOW,
  );

  it(
    "run control off: after Stop, Resume is disabled with the shared line and sends nothing",
    async () => {
      server.runControl = false;
      server.detail = newExperiment();
      await openPage({ shown });
      await confirmStop();
      const resume = await screen.findByTestId("exp-resume");
      await waitFor(() => expect(resume).toBeDisabled());
      expect(resume).toHaveTextContent("Resume 6 left");
      const reason = document.getElementById(
        resume.getAttribute("aria-describedby"),
      );
      expect(reason).toHaveTextContent(NOT_IN_RELEASE);
      fireEvent.click(resume);
      expect(gatedRequests()).toEqual([]);
      expect(server.toasts.some((t) => t.options.key === "exp-resume")).toBe(
        false,
      );
    },
    SLOW,
  );

  it(
    "run control off: a stopped experiment opened cold asks once, then Resume is disabled with the shared line",
    async () => {
      server.runControl = false;
      server.detail = newExperiment("Cancelled");
      await openPage({ shown });
      // Nothing on the page says yet whether this server resumes runs.
      const resume = screen.getByTestId("exp-resume");
      expect(resume).toBeEnabled();
      fireEvent.click(resume);
      await waitFor(() =>
        expect(lastToast("exp-resume")?.message).toBe(NOT_IN_RELEASE),
      );
      await waitFor(() =>
        expect(screen.getByTestId("exp-resume")).toBeDisabled(),
      );
      fireEvent.click(screen.getByTestId("exp-resume"));
      expect(server.requests).toEqual([
        { method: "post", url: resumeUrl, body: { run_ids: [RUN] } },
      ]);
    },
    SLOW,
  );

  it(
    "run control off: a later Stop sends only the empty-body stop",
    async () => {
      server.runControl = false;
      server.detail = newExperiment();
      await openPage({ shown });
      await confirmStop();
      await waitFor(() => expect(server.requests).toHaveLength(2));
      // The experiment runs again (for example a rerun) in the same session.
      server.requests.length = 0;
      server.detail = newExperiment();
      server.emit();
      await screen.findByTestId("exp-stop");
      await confirmStop();
      await waitFor(() =>
        expect(server.requests).toEqual([
          { method: "post", url: stopUrl, body: {} },
        ]),
      );
      expect(gatedRequests()).toEqual([]);
    },
    SLOW,
  );

  it(
    "run control on: Stop and Resume name the runs, as before",
    async () => {
      server.runControl = true;
      server.detail = newExperiment();
      await openPage({ shown });
      await confirmStop();
      await waitFor(() =>
        expect(server.requests).toEqual([
          { method: "post", url: stopUrl, body: { run_ids: [RUN] } },
        ]),
      );
      const toast = lastToast("exp-stop");
      expect(toast.message).toBe("Stopped #1A · 4 of 10 rows kept");
      expect(typeof toast.options.undo?.onUndo).toBe("function");
      const resume = await screen.findByTestId("exp-resume");
      expect(resume).toBeEnabled();
      fireEvent.click(resume);
      await waitFor(() =>
        expect(server.requests).toEqual([
          { method: "post", url: stopUrl, body: { run_ids: [RUN] } },
          { method: "post", url: resumeUrl, body: { run_ids: [RUN] } },
        ]),
      );
      // Once the server has paused a run, Stop says the rest can resume.
      server.detail = newExperiment();
      server.emit();
      fireEvent.click(await screen.findByTestId("exp-stop"));
      const dialog = await screen.findByRole("dialog");
      expect(dialog).toHaveTextContent("You can resume the others later.");
    },
    SLOW,
  );

  it(
    "an experiment made before runs were recorded still stops while it runs",
    async () => {
      server.runControl = false;
      server.detail = olderExperiment("Running");
      await openPage({ shown });
      await confirmStop("Stop experiment");
      await waitFor(() =>
        expect(server.requests).toEqual([
          { method: "post", url: stopUrl, body: {} },
        ]),
      );
      expect(lastToast("exp-stop").message).toBe(
        "Stopped Ticket triage (before runs)",
      );
      expect(gatedRequests()).toEqual([]);
    },
    SLOW,
  );

  it(
    "an experiment made before runs were recorded offers no Stop once it finished",
    async () => {
      server.runControl = false;
      server.detail = olderExperiment("Completed");
      await openPage({ shown });
      expect(screen.queryByTestId("exp-stop")).toBeNull();
      expect(server.requests).toEqual([]);
    },
    SLOW,
  );
});
