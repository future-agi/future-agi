// A pause is not a new attempt. Exercise the real header and its
// eligibility helpers; only reads, permissions, HTTP and unrelated UI seams
// are replaced. Canonical run.status is lowercase, unlike progress.status.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios, { endpoints } from "src/utils/axios";
import {
  noteRunControl,
  resetRunControlForTest,
} from "src/sections/common/ExperimentRunControl/runControlAvailability";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const RUN = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e4f";
const NATIVE = "8b6e4e07-950f-41e0-a54a-f2fb597b9a31";
const SEALED = "2026-11-15T06:00:00Z";
const RESUME_FIRST =
  "Resume the paused runs to finish remaining rows before rerunning.";
const STOP_AGAIN =
  "Stopping didn't finish; press Stop again to complete it before rerunning.";

const server = vi.hoisted(() => ({
  detail: null,
  role: "Owner",
  canRun: true,
  requests: [],
  listeners: new Set(),
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
    useDatasetRuns: () => ({
      data: { results: server.detail.runs, pinned_run_id: null },
    }),
  };
});
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "ds", name: "Tickets" }],
  }),
}));
vi.mock("src/api/develop/r2", () => ({
  useMatrix: () => ({ data: { rows: [] } }),
  useRunConfigs: () => ({ data: { results: server.detail.runs } }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: server.role }),
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: {
      view: true,
      download: true,
      edit: true,
      run: server.canRun,
      manage: false,
    },
    reason: () => (server.canRun ? null : "Run access is required."),
    ask: [],
    isLoading: false,
    view: null,
  }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "rows" }),
}));
vi.mock("src/sections/common/ux/toast", async (importOriginal) => {
  const notify = vi.fn(() => ({ undone: Promise.resolve(false) }));
  return { ...(await importOriginal()), default: notify, notify };
});
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
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

import ExperimentRow from "../ExperimentRow";

function run({ status = "cancelled", done = 4, total = 10, ...fields } = {}) {
  return {
    run_id: RUN,
    experiment_id: EXP,
    native_config_id: NATIVE,
    completed_at: null,
    label: "#1A",
    name: "Prompt",
    status,
    progress: {
      status: status[0].toUpperCase() + status.slice(1),
      total,
      done,
      errors: 0,
    },
    config: { kind: "prompt" },
    ...fields,
  };
}

function experiment(runs = [run()], fields = {}) {
  return {
    id: EXP,
    ordinal: 1,
    name: "Ticket triage",
    dataset_id: "ds",
    status: runs[0]?.progress?.status ?? "Cancelled",
    runs,
    runs_total: runs.length,
    http_configs: [],
    results_availability: { reason: null },
    ...fields,
  };
}

const completed = () =>
  experiment([run({ status: "completed", done: 10, completed_at: SEALED })]);
const stopUrl = endpoints.develop.experiment.stop(EXP);
const resumeUrl = endpoints.develop.datasetRuns.resume(EXP);
const rerunUrl = endpoints.develop.experiment.rerun;
const cellRerunUrl = endpoints.develop.experiment.reRunExperimentCell(EXP);
const rerunRequests = () =>
  server.requests.filter(({ url }) => url === rerunUrl || url === cellRerunUrl);
const rerunButton = () => screen.getByRole("button", { name: /^Rerun(?:$| ·)/ });

let queryClient;
function openPage(detail = experiment(), runControl = "on") {
  server.detail = detail;
  if (runControl !== "unknown") noteRunControl(runControl);
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
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

// Model a detail poll without remounting the header: menus/confirms stay live.
async function publish(detail) {
  await act(async () => {
    server.detail = detail;
    server.listeners.forEach((listener) => listener());
  });
}

async function click(element) {
  await act(async () => {
    fireEvent.click(element);
  });
}

async function openRerunConfirm() {
  await click(rerunButton());
  const all = await screen.findByTestId("exp-rerun-all");
  expect(all).not.toHaveAttribute("aria-disabled", "true");
  await click(all);
  const dialog = await screen.findByRole("dialog");
  return within(dialog).getByRole("button", { name: "Re-run all" });
}

async function rerunSuccessfully() {
  expect(rerunButton()).toBeEnabled();
  await click(await openRerunConfirm());
  await waitFor(() =>
    expect(rerunRequests()).toEqual([
      { method: "post", url: rerunUrl, body: { experiment_ids: [EXP] } },
    ]),
  );
}

function expectNoEnabledResume() {
  const resume = screen.queryByTestId("exp-resume");
  if (resume) expect(resume).toBeDisabled();
}

beforeEach(() => {
  window.innerWidth = 1600;
  resetRunControlForTest();
  server.requests.length = 0;
  server.listeners.clear();
  server.role = "Owner";
  server.canRun = true;
  // Keep the real API action functions: this records their actual URL/body,
  // including whether a Stop request names runs or sends an empty object.
  vi.spyOn(axios, "post").mockImplementation(async (url, body) => {
    server.requests.push({ method: "post", url, body });
    if (url === resumeUrl) {
      return {
        status: 202,
        data: {
          status: true,
          result: {
            experiment_id: EXP,
            runs: [{ run_id: RUN, pending: 6 }],
          },
        },
      };
    }
    if (url === stopUrl) {
      return {
        status: 200,
        data: {
          status: true,
          result: {
            experiment_id: EXP,
            message: "Experiment stopped.",
            workflows_cancelled: { main: true, reruns: 0 },
            rows_in_flight: 0,
          },
        },
      };
    }
    if (url === rerunUrl || url === cellRerunUrl) {
      return { status: 200, data: { status: true, result: {} } };
    }
    throw new Error(`Unexpected POST in Rerun regression: ${url}`);
  });
});

afterEach(() => {
  cleanup();
  queryClient?.clear();
  resetRunControlForTest();
  axios.post.mockRestore();
});

describe("Rerun waits for resumable paused work", () => {
  it.each(["cancelled", "Cancelled"])(
    "%s with rows left disables Rerun with an accessible Resume reason",
    async (status) => {
      openPage(experiment([run({ status })]));
      const rerun = rerunButton();
      expect(rerun).toBeDisabled();
      expect(rerun).toHaveAccessibleDescription(RESUME_FIRST);
      const resume = screen.getByTestId("exp-resume");
      expect(resume).toBeEnabled();
      expect(resume).toHaveTextContent("Resume 6 left");
      await click(rerun);
      expect(screen.queryByTestId("exp-rerun-menu")).toBeNull();
      expect(server.requests).toEqual([]);
    },
  );

  it("blocks Rerun before run-control availability has been learned", () => {
    openPage(experiment(), "unknown");
    expect(rerunButton()).toBeDisabled();
    expect(rerunButton()).toHaveAccessibleDescription(RESUME_FIRST);
    expect(screen.getByTestId("exp-resume")).toBeEnabled();
    expect(server.requests).toEqual([]);
  });

  it("one paused run blocks Rerun even when another run completed", () => {
    openPage(
      experiment([
        run({
          run_id: "7921501a-e739-4638-9424-3c962b006326",
          native_config_id: "54ffcd7f-a9e5-4888-9632-75d4a11f7d34",
          label: "#1B",
          status: "completed",
          done: 10,
          completed_at: SEALED,
        }),
        run(),
      ]),
    );
    expect(rerunButton()).toBeDisabled();
    expect(rerunButton()).toHaveAccessibleDescription(RESUME_FIRST);
    expect(screen.getByTestId("exp-resume")).toHaveTextContent("Resume 6 left");
  });

  it("an already-open All rows menu becomes disabled when a poll reports paused work", async () => {
    openPage(completed());
    await click(rerunButton());
    const all = await screen.findByTestId("exp-rerun-all");
    expect(all).not.toHaveAttribute("aria-disabled", "true");
    await publish(experiment());
    // Closing the menu is also safe. If it stays open its menuitem must
    // expose disabled semantics (MUI menuitems are not native buttons).
    await waitFor(() => {
      if (screen.queryByRole("menu")) {
        expect(screen.getByTestId("exp-rerun-all")).toHaveAttribute(
          "aria-disabled",
          "true",
        );
      }
    });
    await click(screen.queryByTestId("exp-rerun-all") ?? all);
    // Even a stale activation must not progress to an invalid dispatch.
    const confirm = screen.queryByRole("button", { name: "Re-run all" });
    if (confirm) await click(confirm);
    expect(server.requests).toEqual([]);
  });

  it("an already-open Rerun confirmation cannot POST after a poll reports paused work", async () => {
    openPage(completed());
    const confirm = await openRerunConfirm();
    await publish(experiment());
    await click(confirm);
    expect(server.requests).toEqual([]);
  });

  it("Resume names the original run; a completed poll enables Rerun again", async () => {
    openPage();
    const resume = screen.getByTestId("exp-resume");
    expect(resume).toBeEnabled();
    await click(resume);
    await waitFor(() =>
      expect(server.requests).toEqual([
        { method: "post", url: resumeUrl, body: { run_ids: [RUN] } },
      ]),
    );
    // A Resume acknowledgement alone does not create a new attempt or
    // claim completion. The detail still says paused until its next poll.
    expect(server.detail.runs.map(({ run_id }) => run_id)).toEqual([RUN]);
    expect(rerunRequests()).toEqual([]);
    await publish(experiment([run({ status: "running" })]));
    expect(screen.getByTestId("exp-stop")).toBeEnabled();
    await publish(completed());
    expectNoEnabledResume();
    expect(screen.queryByText(RESUME_FIRST)).toBeNull();
    await rerunSuccessfully();
    expect(server.requests).toEqual([
      { method: "post", url: resumeUrl, body: { run_ids: [RUN] } },
      { method: "post", url: rerunUrl, body: { experiment_ids: [EXP] } },
    ]);
  });
});

describe("Rerun controls that must not acquire a false Resume-first block", () => {
  it.each([
    { name: "completed", fields: { status: "completed", done: 10, completed_at: SEALED } },
    { name: "cancelled with no rows left", fields: { done: 10, completed_at: SEALED } },
    { name: "cancelled at its cost cap", fields: { ledger: { stopped_by: "cost_cap" }, completed_at: SEALED } },
    { name: "cancelled with rows left but a non-null terminal seal", fields: { completed_at: SEALED } },
  ])("$name can rerun normally", async ({ fields }) => {
    openPage(experiment([run(fields)]));
    expectNoEnabledResume();
    expect(screen.queryByText(RESUME_FIRST)).toBeNull();
    await rerunSuccessfully();
    expect(server.requests).toEqual(rerunRequests());
  });

  it("an unsealed cost-cap stop still cannot offer Resume", () => {
    openPage(experiment([run({ ledger: { stopped_by: "cost_cap" } })]));
    expectNoEnabledResume();
    expect(screen.queryByText(RESUME_FIRST)).toBeNull();
    expect(server.requests).toEqual([]);
  });

  it("confirmed legacy whole Stop permits Rerun without directing users to Resume", async () => {
    openPage(experiment([run({ status: "running" })]), "off");
    await click(screen.getByTestId("exp-stop"));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).not.toHaveTextContent(/resume/i);
    await click(within(dialog).getByRole("button", { name: /^Stop (?:run|experiment)$/ }));
    await waitFor(() =>
      expect(server.requests).toEqual([{ method: "post", url: stopUrl, body: {} }]),
    );
    // Only this explicit terminal detail seal confirms cleanup. A 200
    // response by itself is not assumed to seal every legacy Stop.
    await publish(experiment([run({ completed_at: SEALED })]));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expectNoEnabledResume();
    expect(screen.queryByText(RESUME_FIRST)).toBeNull();
    expect(screen.queryByText(STOP_AGAIN)).toBeNull();
    expect(screen.queryByTestId("exp-stop")).toBeNull();
    await rerunSuccessfully();
    expect(server.requests).toEqual([
      { method: "post", url: stopUrl, body: {} },
      { method: "post", url: rerunUrl, body: { experiment_ids: [EXP] } },
    ]);
  });
});

describe("Unsealed native legacy Stop recovery", () => {
  it("explicit null seal blocks Rerun and offers empty-body Stop again, then releases after a terminal poll", async () => {
    openPage(experiment(), "off");
    const rerun = rerunButton();
    expect(rerun).toBeDisabled();
    expect(rerun).toHaveAccessibleDescription(STOP_AGAIN);
    expect(screen.queryByText(RESUME_FIRST)).toBeNull();
    expectNoEnabledResume();
    await click(rerun);
    expect(server.requests).toEqual([]);
    await click(screen.getByTestId("exp-stop"));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).not.toHaveTextContent(/resume/i);
    await click(within(dialog).getByRole("button", { name: "Stop experiment" }));
    await waitFor(() =>
      expect(server.requests).toEqual([{ method: "post", url: stopUrl, body: {} }]),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // Even a successful Stop HTTP response does not invent a terminal seal.
    expect(rerunButton()).toBeDisabled();
    await publish(experiment([run({ completed_at: SEALED })]));
    expect(screen.queryByText(STOP_AGAIN)).toBeNull();
    expectNoEnabledResume();
    expect(screen.queryByTestId("exp-stop")).toBeNull();
    await rerunSuccessfully();
    expect(server.requests).toEqual([
      { method: "post", url: stopUrl, body: {} },
      { method: "post", url: rerunUrl, body: { experiment_ids: [EXP] } },
    ]);
  });

  it.each([
    { name: "an absent completed_at field", omit: "completed_at", fields: {} },
    { name: "an absent native binding", omit: "native_config_id", fields: {} },
    { name: "a null native binding", fields: { native_config_id: null } },
  ])("does not invent a stuck native binding for $name", async ({ omit, fields }) => {
    const legacy = run(fields);
    if (omit) delete legacy[omit];
    openPage(experiment([legacy]), "off");
    expect(screen.queryByText(RESUME_FIRST)).toBeNull();
    expect(screen.queryByText(STOP_AGAIN)).toBeNull();
    expect(screen.queryByTestId("exp-stop")).toBeNull();
    expectNoEnabledResume();
    await rerunSuccessfully();
    expect(server.requests).toEqual(rerunRequests());
  });

  it("an HTTP-only legacy experiment is not presented as unsealed native work", () => {
    // Older HTTP experiments can lack canonical run identities.
    openPage(
      experiment([], {
        status: "cancelled",
        http_configs: [{ id: "http-source" }],
        results_availability: { reason: "native_identity_not_recorded" },
      }),
      "off",
    );
    expect(screen.queryByText(RESUME_FIRST)).toBeNull();
    expect(screen.queryByText(STOP_AGAIN)).toBeNull();
    expect(screen.queryByTestId("exp-stop")).toBeNull();
    expectNoEnabledResume();
    expect(server.requests).toEqual([]);
  });
});

describe("Canonical HTTP legacy control", () => {
  it("does not classify an HTTP binding as unfinished native prompt work", () => {
    openPage(
      experiment([
        run({ source: "http", task_kind: "external", config: { kind: "http" } }),
      ], { http_configs: [{ id: "http-source" }] }),
      "off",
    );
    expect(screen.queryByText(STOP_AGAIN)).toBeNull();
    expect(screen.queryByText(RESUME_FIRST)).toBeNull();
    expect(screen.queryByTestId("exp-stop")).toBeNull();
    expectNoEnabledResume();
    expect(rerunButton()).toBeEnabled();
    expect(server.requests).toEqual([]);
  });
});

describe("Rerun and Resume preserve permission checks", () => {
  it.each([
    { name: "read-only role", role: "Viewer", canRun: true },
    { name: "dataset run access denied", role: "Owner", canRun: false },
  ])("$name cannot rerun or resume", async ({ role, canRun }) => {
    server.role = role;
    server.canRun = canRun;
    openPage();
    expect(screen.queryByTestId("exp-rerun-anchor")).toBeNull();
    expect(screen.queryByTestId("exp-resume")).toBeNull();
    expect(screen.queryByTestId("exp-stop")).toBeNull();
    await publish(completed());
    expect(screen.queryByTestId("exp-rerun-anchor")).toBeNull();
    expect(screen.queryByTestId("exp-resume")).toBeNull();
    expect(server.requests).toEqual([]);
  });
});
