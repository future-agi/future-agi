// The experiment page's Stop confirm while the runs it names end (they
// finish, fail or are stopped elsewhere; the page's poll updates). Confirming
// then must send nothing and say so in plain words, never "Select at least
// one run to stop." and never a stop for runs the confirm did not name.
// Requests go through the page's real request functions; only the API
// client's transport is a stand-in that records each request.
/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  noteRunControl,
  resetRunControlForTest,
} from "src/sections/common/ExperimentRunControl/runControlAvailability";
import ExperimentRow from "../ExperimentRow";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const RUN_A = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e4f";
const RUN_B = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d";
const RUN_C = "2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e";

const server = vi.hoisted(() => ({
  runControl: true,
  sent: [],
  detail: null,
  listeners: new Set(),
  toasts: [],
  emit() {
    this.listeners.forEach((listener) => listener());
  },
  async post(url, body) {
    this.sent.push({ method: "post", url, body });
    if (url.endsWith("/stop/") && body?.run_ids && !this.runControl)
      throw {
        status: false,
        code: "run_control_not_enabled",
        detail: "Run control is not available.",
        message: "Run control is not available.",
        statusCode: 404,
      };
    return {
      status: body?.run_ids ? 202 : 200,
      data: {
        status: true,
        result: {
          experiment_id: url.split("/")[4],
          runs: (body?.run_ids ?? []).map((id) => ({
            run_id: id,
            done: 4,
            total: 10,
          })),
          workflows_cancelled: { main: true, reruns: 0 },
          message: "Experiment stopped.",
        },
      },
    };
  },
}));

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
vi.mock("../DevelopExperimentSelect", () => ({
  default: () => <span>Ticket triage</span>,
  experimentStatusOf: () => null,
}));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));

const runOf = (runId, label, status = "Running") => ({
  run_id: runId,
  experiment_id: EXP,
  experiment_ordinal: 1,
  label,
  name: "Prompt",
  status,
  progress: { total: 10, done: 4, errors: 0 },
});
const experimentWith = (runs, status = "Running") => ({
  id: EXP,
  name: "Ticket triage",
  dataset_id: "ds",
  status,
  runs,
  runs_total: runs.length,
  results_availability: {
    reason: runs.length ? null : "native_identity_not_recorded",
  },
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

// The page's poll brings the new state while the confirm is open.
const poll = (detail) =>
  act(() => {
    server.detail = detail;
    server.emit();
  });

async function openConfirm() {
  fireEvent.click(screen.getByTestId("exp-stop"));
  return screen.findByRole("dialog");
}

async function confirm(dialog, label) {
  fireEvent.click(within(dialog).getByRole("button", { name: label }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
}

const posts = () => server.sent.filter((r) => r.method === "post");
const stopToast = () =>
  [...server.toasts].reverse().find((t) => t.options.key === "exp-stop");
const stopUrl = `/model-hub/experiments/v2/${EXP}/stop/`;

beforeEach(() => {
  window.innerWidth = 1600;
  resetRunControlForTest();
  server.runControl = true;
  server.sent.length = 0;
  server.toasts.length = 0;
  server.listeners.clear();
});

describe("Stop confirmed after the runs it names ended", () => {
  it("sends nothing and says the run is no longer running", async () => {
    server.detail = experimentWith([runOf(RUN_A, "#1A")]);
    openPage();
    const dialog = await openConfirm();
    await poll(experimentWith([runOf(RUN_A, "#1A", "Completed")], "Completed"));
    const shown = dialog.textContent;
    await confirm(dialog, "Stop run");
    await waitFor(() =>
      expect(stopToast()?.message).toBe(
        "This run is no longer running; nothing was stopped.",
      ),
    );
    expect(stopToast().options.variant).toBe("info");
    expect(posts()).toEqual([]);
    expect(
      server.toasts.some((t) => /select at least one run/i.test(t.message)),
    ).toBe(false);
    // The confirm kept naming the run it was opened for.
    expect(shown).toContain("Stop #1A?");
  });

  it("with run control off on this server, it still sends nothing", async () => {
    server.runControl = false;
    noteRunControl("off");
    server.detail = experimentWith([runOf(RUN_A, "#1A"), runOf(RUN_B, "#1B")]);
    openPage();
    const dialog = await openConfirm();
    await poll(
      experimentWith(
        [runOf(RUN_A, "#1A", "Completed"), runOf(RUN_B, "#1B", "Failed")],
        "Completed",
      ),
    );
    await confirm(dialog, "Stop run");
    await waitFor(() =>
      expect(stopToast()?.message).toBe(
        "These runs are no longer running; nothing was stopped.",
      ),
    );
    expect(posts()).toEqual([]);
  });

  it("stops only the named runs still running, never a run it did not name", async () => {
    server.detail = experimentWith([runOf(RUN_A, "#1A"), runOf(RUN_B, "#1B")]);
    openPage();
    const dialog = await openConfirm();
    // #1A finished and a new run #1C started while the confirm was open.
    await poll(
      experimentWith([
        runOf(RUN_A, "#1A", "Completed"),
        runOf(RUN_B, "#1B"),
        runOf(RUN_C, "#1C"),
      ]),
    );
    const shown = dialog.textContent;
    await confirm(dialog, "Stop run");
    await waitFor(() =>
      expect(posts()).toEqual([
        { method: "post", url: stopUrl, body: { run_ids: [RUN_B] } },
      ]),
    );
    expect(shown).toContain("Stop #1A, #1B?");
  });

  it("an experiment with no run rows that ended while open sends nothing", async () => {
    server.detail = experimentWith([], "Running");
    openPage();
    const dialog = await openConfirm();
    await poll(experimentWith([], "Completed"));
    await confirm(dialog, "Stop experiment");
    await waitFor(() =>
      expect(stopToast()?.message).toBe(
        "This experiment is no longer running; nothing was stopped.",
      ),
    );
    expect(posts()).toEqual([]);
  });
});
