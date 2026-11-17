// The experiment page's Stop on the default path (run control off): the
// page's run-id stop is refused as not enabled and the page sends the
// empty-body stop, which stops the experiment whole. Rows whose call was
// already sent still finish, and are saved and counted; the stop answers how
// many are in flight. While any are, the toast gives no count, only that rows
// already sent will finish and be kept; with none in flight the count is
// final and shown, and so it is when the answer has no count (an older
// server, or a Stop that only withdrew a run not yet started). With an HTTP
// source (the detail's http_configs: an HTTP source has no run of its own in
// the catalog), whose late answer is not saved, the toast says only that rows
// already sent will finish. The detail fixture has the response's own shape.
//
// Requests go through the page's real request functions; the API client is
// the only stand-in. Both launch-switch states are pinned as the launch-gate
// tests do.
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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { resetRunControlForTest } from "src/sections/common/ExperimentRunControl/runControlAvailability";
import ExperimentRow from "../ExperimentRow";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const RUN_A = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e4f";
const IN_FLIGHT_TEXT =
  "Stopped #1A. Rows already sent will finish and be kept.";

const server = vi.hoisted(() => ({
  // What the whole stop answers for rows in flight (undefined: no field).
  inFlight: undefined,
  sent: [],
  detail: null,
  listeners: new Set(),
  toasts: [],
  emit() {
    this.listeners.forEach((listener) => listener());
  },
  async post(url, body) {
    this.sent.push({ method: "post", url, body });
    if (url.endsWith("/stop/") && body?.run_ids)
      throw {
        status: false,
        code: "run_control_not_enabled",
        detail: "Run control is not available.",
        message: "Run control is not available.",
        statusCode: 404,
      };
    if (url.endsWith("/stop/"))
      return {
        status: 200,
        data: {
          status: true,
          result: {
            message: "Experiment stopped.",
            experiment_id: url.split("/")[4],
            workflows_cancelled: { main: true, reruns: 0 },
            ...(this.inFlight === undefined
              ? {}
              : { rows_in_flight: this.inFlight }),
          },
        },
      };
    return { status: 200, data: { status: true, result: {} } };
  },
}));
const unreleased = vi.hoisted(() => ({ shown: true }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const real = actual.default;
  const record = (method) => async (url, body) => {
    server.sent.push({ method, url, body });
    return { status: 200, data: { status: true, result: {} } };
  };
  return {
    ...actual,
    default: {
      defaults: real.defaults,
      interceptors: real.interceptors,
      get: record("get"),
      post: (url, body) => server.post(url, body),
      put: record("put"),
      patch: record("patch"),
      delete: record("delete"),
    },
  };
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
vi.mock("../DevelopExperimentSelect", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <span>Ticket triage</span>,
}));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});

const experiment = () => ({
  id: EXP,
  name: "Ticket triage",
  dataset_id: "ds",
  status: "Running",
  runs: [
    {
      run_id: RUN_A,
      experiment_id: EXP,
      experiment_ordinal: 1,
      label: "#1A",
      name: "Prompt",
      status: "running",
      completed_at: null,
      progress: { total: 10, done: 4, scored: 4, errors: 0 },
    },
  ],
  runs_total: 1,
  prompt_configs: [],
  agent_configs: [],
  http_configs: [],
  results_availability: { reason: null },
});

// One saved HTTP source, as the detail read returns it (http_configs[]).
const HTTP_SOURCE = {
  id: "9b2c4e6a-1d3f-4a5b-8c7d-0e1f2a3b4c5d",
  order: 1,
  http: {
    url: "https://fixture.example/run",
    method: "POST",
    body_template: "{}",
    output_path: "$.reply",
  },
  variable_mapping: {},
};

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

async function headerStop() {
  fireEvent.click(screen.getByTestId("exp-stop"));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Stop run" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
}
const stopToasts = () =>
  server.toasts.filter((t) => t.options.key === "exp-stop");
const stopUrl = `/model-hub/experiments/v2/${EXP}/stop/`;
const stops = () => server.sent.filter((r) => r.url === stopUrl);

beforeEach(() => {
  window.innerWidth = 1600;
  resetRunControlForTest();
  server.inFlight = undefined;
  server.sent.length = 0;
  server.toasts.length = 0;
  server.listeners.clear();
  server.detail = experiment();
});

describe.each([
  { state: "switch on (shipped build)", shown: true },
  { state: "switch off", shown: false },
])("the default path's Stop toast, $state", ({ shown }) => {
  beforeEach(() => {
    unreleased.shown = shown;
    setLaunchOverrideForTest(
      shown
        ? { launch: true, experimentsBasic: true }
        : { launch: true, showUnreleased: false, experimentsBasic: true },
    );
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
  });

  it("rows in flight: no count, only that they will finish and be kept", async () => {
    server.inFlight = 2;
    openPage();
    await headerStop();
    await waitFor(() => expect(stopToasts()).toHaveLength(1));
    expect(stopToasts()[0].message).toBe(IN_FLIGHT_TEXT);
    expect(stopToasts()[0].message).not.toMatch(/\d+ of \d+/);
    expect(stops().map((r) => r.body)).toEqual([{ run_ids: [RUN_A] }, {}]);
  });

  it("with an HTTP source: they will finish, and nothing says kept", async () => {
    server.inFlight = 2;
    server.detail = { ...experiment(), http_configs: [HTTP_SOURCE] };
    openPage();
    await headerStop();
    await waitFor(() => expect(stopToasts()).toHaveLength(1));
    expect(stopToasts()[0].message).toBe(
      "Stopped #1A. Rows already sent will finish.",
    );
    expect(stopToasts()[0].message).not.toMatch(/kept/);
  });

  it("an answer without the count: no claim that rows are in flight", async () => {
    openPage();
    await headerStop();
    await waitFor(() => expect(stopToasts()).toHaveLength(1));
    expect(stopToasts()[0].message).toBe("Stopped #1A · 4 of 10 rows kept");
    expect(stopToasts()[0].message).not.toMatch(/will finish/);
  });

  it("no row in flight: the count is final and shown", async () => {
    server.inFlight = 0;
    openPage();
    await headerStop();
    await waitFor(() => expect(stopToasts()).toHaveLength(1));
    expect(stopToasts()[0].message).toBe("Stopped #1A · 4 of 10 rows kept");
  });
});
