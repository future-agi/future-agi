// The experiment page's Stop requests in both run-control states, through
// the page's real request functions (the API client is the only stand-in; it
// records each request and answers as the server does). The page's header
// Stop and a run's own Stop (the results run chip) each name exactly what
// they stop, with nothing else in the body:
// - run control on: the header Stop names every active run; a run's Stop
//   names only that run; a refusal of any other kind (for example a run the
//   server cannot find) is shown and never turns into the empty-body stop;
// - run control off: the header Stop stops the experiment with the
//   empty-body stop; a run's own Stop and Resume are disabled with the
//   shared line and send nothing;
// - in both states, an experiment running with no run to name (made before
//   runs were recorded, or whose recorded runs all ended) still gets the
//   header Stop, which sends only production's empty-body stop.
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
import { strictViolations } from "src/api/contracts/strictBody";
import { useExperimentDetail } from "src/api/develop/runs";
import { resetRunControlForTest } from "src/sections/common/ExperimentRunControl/runControlAvailability";
import ExperimentRow from "../ExperimentRow";
import RunBar from "../results/RunBar";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const RUN_A = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e4f";
const RUN_B = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d";
const STOP_HELD = `${NOT_IN_RELEASE}. Stop the whole experiment from its page.`;

const server = vi.hoisted(() => ({
  runControl: false,
  // A refusal the server sends for a run-id stop even with run control on.
  refuseRunIds: null,
  sent: [],
  detail: null,
  listeners: new Set(),
  toasts: [],
  emit() {
    this.listeners.forEach((listener) => listener());
  },
  // The app's API client rejects with the response body and its statusCode.
  refusal(statusCode, code, detail) {
    return { status: false, code, detail, message: detail, statusCode };
  },
  // The experiment reads Cancelled once none of its runs still runs, as
  // the server projects it.
  markStopped(ids) {
    const stops = (r) => !ids || ids.includes(r.run_id);
    const runs = this.detail.runs.map((r) =>
      stops(r) ? { ...r, status: "Cancelled" } : r,
    );
    const running = runs.some((r) => r.status === "Running");
    this.detail = {
      ...this.detail,
      status: !ids || !running ? "Cancelled" : this.detail.status,
      runs,
    };
    this.emit();
  },
  async post(url, body) {
    this.sent.push({ method: "post", url, body });
    if (url.endsWith("/stop/") && body?.run_ids) {
      if (!this.runControl)
        throw this.refusal(
          404,
          "run_control_not_enabled",
          "Run control is not available.",
        );
      if (this.refuseRunIds) throw this.refuseRunIds;
      this.markStopped(body.run_ids);
      return {
        status: 202,
        data: {
          status: true,
          result: {
            experiment_id: url.split("/")[4],
            runs: body.run_ids.map((id) => ({
              run_id: id,
              name: "Prompt",
              status: "STOPPED",
              done: 4,
              total: 10,
            })),
          },
        },
      };
    }
    if (url.endsWith("/stop/")) {
      this.markStopped();
      return {
        status: 200,
        data: {
          status: true,
          result: {
            message: "Experiment stopped.",
            experiment_id: url.split("/")[4],
            workflows_cancelled: { main: true, reruns: 0 },
          },
        },
      };
    }
    if (url.endsWith("/resume/") && !this.runControl)
      throw this.refusal(
        404,
        "run_control_not_enabled",
        "Run control is not available.",
      );
    return { status: 200, data: { status: true, result: {} } };
  },
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const real = actual.default;
  const client = {
    defaults: real.defaults,
    interceptors: real.interceptors,
    get: async (url) => {
      server.sent.push({ method: "get", url });
      return { status: 200, data: { status: true, result: {} } };
    },
    post: (url, body) => server.post(url, body),
    put: async (url, body) => {
      server.sent.push({ method: "put", url, body });
      return { status: 200, data: {} };
    },
    patch: async (url, body) => {
      server.sent.push({ method: "patch", url, body });
      return { status: 200, data: {} };
    },
    delete: async (url) => {
      server.sent.push({ method: "delete", url });
      return { status: 200, data: {} };
    },
  };
  return { ...actual, default: client };
});
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

const runOf = (runId, label) => ({
  run_id: runId,
  experiment_id: EXP,
  experiment_ordinal: 1,
  label,
  name: "Prompt",
  status: "Running",
  progress: { total: 10, done: 4, errors: 0 },
});
// Made the production way: no run recorded, so no run id to name.
const runningWithoutRuns = () => ({
  id: EXP,
  name: "Ticket triage",
  dataset_id: "ds",
  status: "Running",
  runs: [],
  runs_total: 0,
  results_availability: { reason: "native_identity_not_recorded" },
});
// Its recorded runs all ended, while the experiment still runs.
const runningWithEndedRuns = () => ({
  ...runningExperiment(),
  runs: runningExperiment().runs.map((r) => ({ ...r, status: "Completed" })),
});
const runningExperiment = () => ({
  id: EXP,
  name: "Ticket triage",
  dataset_id: "ds",
  status: "Running",
  runs: [runOf(RUN_A, "#1A"), runOf(RUN_B, "#1B")],
  runs_total: 2,
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
        baselineId={RUN_A}
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

async function chipItem(label, name) {
  fireEvent.click(screen.getByLabelText(`${label} menu`));
  return screen.findByRole("menuitem", { name });
}

async function headerStop(confirmLabel = "Stop run") {
  fireEvent.click(screen.getByTestId("exp-stop"));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: confirmLabel }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
}

const stopUrl = `/model-hub/experiments/v2/${EXP}/stop/`;
const posts = () => server.sent.filter((r) => r.method === "post");
const gated = () =>
  server.sent.filter(
    (r) => r.url.endsWith("/resume/") || r.url.endsWith("/run-control/"),
  );
const clean = (requests) =>
  requests.flatMap((r) => strictViolations(r.method, r.url, r.body));

beforeEach(() => {
  window.innerWidth = 1600;
  resetRunControlForTest();
  server.runControl = false;
  server.refuseRunIds = null;
  server.sent.length = 0;
  server.toasts.length = 0;
  server.listeners.clear();
  server.detail = runningExperiment();
});

describe("run control on", () => {
  beforeEach(() => {
    server.runControl = true;
  });

  it("the page's Stop names every active run, and nothing else", async () => {
    openPage();
    await headerStop();
    await waitFor(() =>
      expect(posts()).toEqual([
        { method: "post", url: stopUrl, body: { run_ids: [RUN_A, RUN_B] } },
      ]),
    );
    expect(clean(posts())).toEqual([]);
  });

  it("a run's own Stop names only that run, and the page's Stop then names the rest", async () => {
    openPage();
    fireEvent.click(await chipItem("#1A", /^Stop #1A/));
    await waitFor(() =>
      expect(posts()).toEqual([
        { method: "post", url: stopUrl, body: { run_ids: [RUN_A] } },
      ]),
    );
    await headerStop();
    await waitFor(() =>
      expect(posts()).toEqual([
        { method: "post", url: stopUrl, body: { run_ids: [RUN_A] } },
        { method: "post", url: stopUrl, body: { run_ids: [RUN_B] } },
      ]),
    );
    expect(clean(posts())).toEqual([]);
    expect(gated()).toEqual([]);
  });

  it("a run the server cannot find is shown and never becomes the empty-body stop", async () => {
    server.refuseRunIds = server.refusal(404, "not_found", "Run not found.");
    openPage();
    await headerStop();
    await waitFor(() =>
      expect(
        server.toasts.find((t) => t.options.key === "exp-stop")?.options
          .variant,
      ).toBe("error"),
    );
    expect(posts()).toEqual([
      { method: "post", url: stopUrl, body: { run_ids: [RUN_A, RUN_B] } },
    ]);
    fireEvent.click(await chipItem("#1A", /^Stop #1A/));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[1]).toEqual({
      method: "post",
      url: stopUrl,
      body: { run_ids: [RUN_A] },
    });
    expect(posts().filter((r) => !r.body.run_ids)).toEqual([]);
  });
});

describe("run control off", () => {
  it("the page's Stop stops the experiment with the empty-body stop", async () => {
    openPage();
    await headerStop();
    await waitFor(() =>
      expect(posts()).toEqual([
        { method: "post", url: stopUrl, body: { run_ids: [RUN_A, RUN_B] } },
        { method: "post", url: stopUrl, body: {} },
      ]),
    );
    expect(clean(posts())).toEqual([]);
    await waitFor(() => expect(screen.queryByTestId("exp-stop")).toBeNull());
    expect(gated()).toEqual([]);
  });

  it("then a run's own Stop and Resume are disabled with the line and send nothing", async () => {
    openPage();
    await headerStop();
    await waitFor(() => expect(posts()).toHaveLength(2));

    // Stopped: each run's Resume is held with the shared line.
    const resume = await chipItem("#1A", /^Resume #1A/);
    expect(resume).toHaveAttribute("aria-disabled", "true");
    expect(within(resume).getByText(NOT_IN_RELEASE)).toBeInTheDocument();
    fireEvent.click(resume);

    // The experiment runs again (for example a rerun): each run's Stop is
    // held with the line, and the page's Stop sends only the empty-body stop.
    server.sent.length = 0;
    server.detail = runningExperiment();
    server.emit();
    await screen.findByTestId("exp-stop");
    for (const label of ["#1A", "#1B"]) {
      // eslint-disable-next-line no-await-in-loop
      const stop = await chipItem(label, new RegExp(`^Stop ${label}`));
      expect(stop).toHaveAttribute("aria-disabled", "true");
      expect(stop).toHaveTextContent(STOP_HELD);
      fireEvent.click(stop);
      fireEvent.keyDown(stop, { key: "Escape" });
      // eslint-disable-next-line no-await-in-loop
      await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
    }
    expect(posts()).toEqual([]);
    await headerStop();
    await waitFor(() =>
      expect(posts()).toEqual([{ method: "post", url: stopUrl, body: {} }]),
    );
    expect(gated()).toEqual([]);
  });
});

describe.each([
  { state: "run control on", on: true },
  { state: "run control off", on: false },
])("no run to name, $state", ({ on }) => {
  beforeEach(() => {
    server.runControl = on;
  });

  it("an experiment with no recorded runs: the page's Stop sends only the empty-body stop", async () => {
    server.detail = runningWithoutRuns();
    openPage();
    await headerStop("Stop experiment");
    await waitFor(() =>
      expect(posts()).toEqual([{ method: "post", url: stopUrl, body: {} }]),
    );
    expect(clean(posts())).toEqual([]);
    expect(
      server.toasts.find((t) => t.options.key === "exp-stop")?.message,
    ).toBe("Stopped Ticket triage");
    await waitFor(() => expect(screen.queryByTestId("exp-stop")).toBeNull());
    expect(gated()).toEqual([]);
  });

  it("an experiment still running after its recorded runs ended: the page's Stop sends only the empty-body stop", async () => {
    server.detail = runningWithEndedRuns();
    openPage();
    await headerStop("Stop experiment");
    await waitFor(() =>
      expect(posts()).toEqual([{ method: "post", url: stopUrl, body: {} }]),
    );
    expect(gated()).toEqual([]);
  });
});
