// A run's Stop on the dataset's Experiments list does what it says, or says
// why not. The list (row menu, status chip, the grid's menu host and the
// Stop confirm) reads its runs through the dataset-runs query, as the tab
// does, so a refresh is what moves a row. The request functions are a
// stand-in server that records each request as the API client sends it;
// the experiment read goes through the app's own client (its GET is
// answered here).
//
// Run control off: a named stop is refused with 404 run_control_not_enabled
// and changes nothing; the stop with no body ends every run of the
// experiment. So the list stops a run with it only when a read made at the
// click shows that run as the experiment's one run going.
/* eslint-disable react/prop-types */
import React, { useCallback, useState } from "react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import {
  noteRunControl,
  resetRunControlForTest,
  runControlState,
} from "src/sections/common/ExperimentRunControl/runControlAvailability";
import RunActionConfirmHost from "src/sections/develop-detail/Experiment/RunActionConfirmHost";
import RunRowMenu from "../RunRowMenu";
import RunRowMenuHost from "../RunRowMenuHost";
import RunStatusCell from "../RunStatusCell";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const RUN4 = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e04";
const RUN5 = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e05";
const RUN6 = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e06";
const SENTENCE =
  "This run can't be stopped on its own while other runs of this experiment are going. Open the experiment to stop all of its runs.";
const ACTIVE = new Set(["Running", "Queued"]);

const server = vi.hoisted(() => ({
  runControl: false,
  // What the dataset's run list answers, and what the experiment read
  // answers. The list can be a poll behind the experiment.
  listRuns: [],
  experimentRuns: [],
  requests: [],
  toasts: [],
  // The API client's error for a refusal (response body + statusCode).
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
  otherError: null,
  // The server's refusal of the stop with no body, and a failed read.
  wholeError: null,
  readFails: false,
  // Runs per page of the experiment read (0: all in one answer).
  runsPerPage: 0,
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
    if (body.run_ids && server.otherError) throw server.otherError;
    if (body.run_ids && !server.runControl) throw server.notEnabled();
    if (!body.run_ids && server.wholeError) throw server.wholeError;
    // The empty-body stop ends every active run of the experiment; a named
    // stop (run control on) pauses the runs it names.
    const stop = (r) =>
      r.experiment_id === experimentId &&
      ACTIVE.has(r.status) &&
      (!body.run_ids || body.run_ids.includes(r.run_id))
        ? { ...r, status: "Cancelled" }
        : r;
    server.listRuns = server.listRuns.map(stop);
    server.experimentRuns = server.experimentRuns.map(stop);
    return body.run_ids
      ? {
          status: 202,
          data: {
            status: true,
            result: {
              experiment_id: experimentId,
              runs: runIds.map((id) => ({ run_id: id, done: 4, total: 40 })),
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
              workflows_cancelled: { main: false, reruns: 1 },
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
    if (!server.runControl) throw server.notEnabled();
    return { status: 202, data: { status: true, result: { runs: [] } } };
  },
}));
vi.mock("src/sections/common/ux/toast", async (importOriginal) => {
  const notify = (message, options = {}) => {
    server.toasts.push({ message, options });
    return { undone: Promise.resolve(false) };
  };
  return { ...(await importOriginal()), default: notify, notify };
});
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunConfigs: () => ({ data: { results: [] } }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../cellContext", () => ({ useCell: (props) => props }));

const run = (run_id, label, status, done = 1) => ({
  run_id,
  experiment_id: EXP,
  experiment_ordinal: 1,
  experiment_name: "Ticket triage",
  label,
  name: "Prompt",
  target_type: "external",
  status,
  progress: { total: 40, done, errors: 0 },
});

// The experiment read (GET experiments/v2/{id}/) answered from the stand-in.
const experimentUrl = `/model-hub/experiments/v2/${EXP}/`;
const realGet = axios.get;
beforeEach(() => {
  vi.spyOn(axios, "get").mockImplementation((url, config) => {
    if (server.runsPerPage && RUNS_PAGE.test(url))
      return Promise.resolve(runsPage(config));
    if (url !== experimentUrl) return realGet.call(axios, url, config);
    server.requests.push({ method: "get", url });
    if (server.readFails) return Promise.reject(new Error("Network Error"));
    const runs = server.experimentRuns.map((r) => ({ ...r }));
    // More runs than the experiment read holds: the rest are read from the
    // dataset's run list, a page at a time.
    const first = server.runsPerPage ? runs.slice(0, server.runsPerPage) : runs;
    return Promise.resolve({
      status: 200,
      data: {
        status: true,
        result: {
          id: EXP,
          dataset_id: "ds",
          runs: first,
          runs_total: runs.length,
          runs_has_more: first.length < runs.length,
          runs_next: first.length < runs.length ? first.length : null,
        },
      },
    });
  });
});

// The dataset's run list for one experiment, `server.runsPerPage` at a time.
const RUNS_PAGE = /\/model-hub\/datasets\/[^/]+\/runs\/$/;
function runsPage(config) {
  const offset = config.params.offset;
  server.requests.push({ method: "get", url: "runs", offset });
  const runs = server.experimentRuns.map((r) => ({ ...r }));
  const results = runs.slice(offset, offset + server.runsPerPage);
  const next = offset + results.length;
  return {
    status: 200,
    data: {
      status: true,
      result: {
        results,
        count: runs.length,
        offset,
        has_more: next < runs.length,
        next: next < runs.length ? next : null,
      },
    },
  };
}
afterEach(() => vi.restoreAllMocks());

// The Experiments list: each row's status chip and actions button, the
// grid's one menu host, and the Stop confirm.
function List() {
  const { data: runs = [] } = useQuery({
    queryKey: ["develop", "dataset-runs", "ds"],
    queryFn: async () => server.listRuns.map((r) => ({ ...r })),
  });
  const [menu, setMenu] = useState(null);
  const closeMenu = useCallback(() => setMenu(null), []);
  const runsPerExperiment = {};
  runs.forEach((r) => {
    runsPerExperiment[r.experiment_id] =
      (runsPerExperiment[r.experiment_id] ?? 0) + 1;
  });
  const context = {
    datasetId: "ds",
    canEdit: true,
    canRun: true,
    navigate: () => {},
    copyText: () => {},
    allRuns: runs,
    runsPerExperiment,
    openRowMenu: setMenu,
    rowMenu: menu,
  };
  return (
    <>
      {runs.map((r) => (
        <div key={r.run_id} data-testid={`row ${r.label}`}>
          <RunStatusCell node={{ group: false }} data={r} context={context} />
          <RunRowMenu node={{ group: false }} data={r} context={context} />
        </div>
      ))}
      <RunRowMenuHost
        menu={menu}
        rows={runs}
        context={context}
        onClose={closeMenu}
      />
      <RunActionConfirmHost />
    </>
  );
}

async function openList() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <List />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByTestId("row #5");
}

const row = (label) => screen.getByTestId(`row ${label}`);
async function openMenu(label) {
  fireEvent.click(screen.getByRole("button", { name: `${label} actions` }));
  return screen.findByRole("menu");
}
// Confirms the open Stop dialog; returns its text.
async function confirmStop(label) {
  const dialog = await screen.findByRole("dialog", { name: `Stop ${label}?` });
  const text = dialog.textContent;
  fireEvent.click(within(dialog).getByRole("button", { name: "Stop run" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  return text;
}

const stopUrl = `/model-hub/experiments/v2/${EXP}/stop/`;
const named = (id) => ({
  method: "post",
  url: stopUrl,
  body: { run_ids: [id] },
});
const read = { method: "get", url: experimentUrl };
const wholeStop = { method: "post", url: stopUrl, body: {} };
const wholeStops = () =>
  server.requests.filter((r) => r.method === "post" && !r.body.run_ids);
const toastsWith = (message) =>
  server.toasts.filter((t) => t.message === message);

beforeEach(() => {
  window.innerWidth = 1600;
  resetRunControlForTest();
  server.runControl = false;
  server.otherError = null;
  server.wholeError = null;
  server.readFails = false;
  server.runsPerPage = 0;
  server.requests.length = 0;
  server.toasts.length = 0;
  server.listRuns = [
    run(RUN4, "#4", "Completed", 40),
    run(RUN5, "#5", "Running"),
  ];
  server.experimentRuns = server.listRuns.map((r) => ({ ...r }));
});

describe("Stop on the Experiments list without run control", () => {
  it("stops the experiment's one running run: the named stop, a read, then the stop with no body; the row reads Stopped after the refresh", async () => {
    // The experiment read has the newest count.
    server.experimentRuns = [
      run(RUN4, "#4", "Completed", 40),
      run(RUN5, "#5", "Running", 3),
    ];
    await openList();
    await openMenu("#5");
    const stop = screen.getByRole("menuitem", { name: "Stop" });
    expect(stop).not.toHaveAttribute("aria-disabled", "true");
    fireEvent.click(stop);
    // The confirm says what will stop.
    const asked = await confirmStop("#5");
    expect(asked).toContain("So far about 1 of 40 rows are done.");
    expect(asked).toContain("Every row done when you stop is kept.");
    expect(asked).not.toContain("resume");

    await waitFor(() =>
      expect(server.requests).toEqual([named(RUN5), read, wholeStop]),
    );
    expect(runControlState()).toBe("off");
    await waitFor(() =>
      expect(within(row("#5")).getByText("Stopped")).toBeInTheDocument(),
    );
    expect(within(row("#4")).getByText("Completed")).toBeInTheDocument();
    const said = toastsWith("Stopped #5 · 3 of 40 rows kept");
    expect(said).toHaveLength(1);
    expect(said[0].options.variant).toBe("success");
    // The experiment was ended whole: there is nothing to resume.
    expect(said[0].options.undo).toBeUndefined();
    expect(toastsWith(SENTENCE)).toEqual([]);
  });

  it("a second run started after the list was read: the read at the click sees it, no stop with no body is sent, and the page says why", async () => {
    await openList();
    // The server now has #6 going too; the list is a poll behind.
    server.experimentRuns = [
      ...server.experimentRuns,
      run(RUN6, "#6", "Running"),
    ];
    server.listRuns = server.experimentRuns.map((r) => ({ ...r }));
    expect(screen.queryByTestId("row #6")).toBeNull();

    await openMenu("#5");
    fireEvent.click(screen.getByRole("menuitem", { name: "Stop" }));
    await confirmStop("#5");
    await waitFor(() => expect(toastsWith(SENTENCE)).toHaveLength(1));
    expect(server.requests).toEqual([named(RUN5), read]);
    expect(wholeStops()).toEqual([]);
    // Said on the page until it is closed, as information.
    expect(toastsWith(SENTENCE)[0].options).toMatchObject({
      variant: "info",
      persist: true,
    });

    // The list refreshed: both runs still run, and #5's Stop now reads as
    // unavailable with the same words.
    await screen.findByTestId("row #6");
    expect(within(row("#5")).getByText("Running")).toBeInTheDocument();
    expect(within(row("#6")).getByText("Running")).toBeInTheDocument();
    await openMenu("#5");
    const held = screen.getByRole("menuitem", { name: "Stop" });
    expect(held).toHaveAttribute("aria-disabled", "true");
    expect(held).toHaveAccessibleDescription(SENTENCE);
    fireEvent.click(held);
    expect(server.requests).toEqual([named(RUN5), read]);
  });

  it("once the page knows run control is off, a Stop the list shows alone still reads the runs first and stops nothing when another is going", async () => {
    noteRunControl("off");
    await openList();
    server.experimentRuns = [
      ...server.experimentRuns,
      run(RUN6, "#6", "Queued"),
    ];
    await openMenu("#5");
    fireEvent.click(screen.getByRole("menuitem", { name: "Stop" }));
    await confirmStop("#5");
    await waitFor(() => expect(toastsWith(SENTENCE)).toHaveLength(1));
    // No named stop (the server already said no), no stop with no body.
    expect(server.requests).toEqual([read]);
  });

  it("a run that ended before the click: nothing is stopped, and the page says so", async () => {
    await openList();
    server.experimentRuns = server.experimentRuns.map((r) => ({
      ...r,
      status: "Completed",
    }));
    await openMenu("#5");
    fireEvent.click(screen.getByRole("menuitem", { name: "Stop" }));
    await confirmStop("#5");
    await waitFor(() =>
      expect(
        toastsWith("This run is no longer running; nothing was stopped."),
      ).toHaveLength(1),
    );
    expect(server.requests).toEqual([named(RUN5), read]);
  });

  it("the status chip's Stop follows the same rule", async () => {
    noteRunControl("off");
    await openList();
    // Alone: the chip's Stop is live and stops the experiment's one run.
    // Its tooltip names the wrapper around the button.
    const chipStop = row("#5").querySelector(
      '[aria-label="Stop run"] button, [title="Stop run"] button',
    );
    expect(chipStop).not.toBeNull();
    expect(chipStop).not.toBeDisabled();
    fireEvent.click(chipStop);
    await confirmStop("#5");
    await waitFor(() => expect(server.requests).toEqual([read, wholeStop]));
  });
});

describe("a held Stop on the list says why, to the eye and to the keyboard", () => {
  beforeEach(() => {
    noteRunControl("off");
    server.listRuns = [run(RUN5, "#5", "Running"), run(RUN6, "#6", "Running")];
    server.experimentRuns = server.listRuns.map((r) => ({ ...r }));
  });

  it("the row menu's Stop stays, reads as unavailable, and the arrow keys reach it with its reason", async () => {
    await openList();
    const menu = await openMenu("#5");
    const stop = within(menu).getByRole("menuitem", { name: "Stop" });
    expect(stop).toHaveAttribute("aria-disabled", "true");
    // The reason is on screen under the label and is the item's description.
    expect(within(stop).getByText(SENTENCE)).toBeInTheDocument();
    expect(stop).toHaveAccessibleDescription(SENTENCE);

    // Keyboard: from the first item, ArrowDown reaches Stop.
    act(() => within(menu).getByRole("menuitem", { name: "Open" }).focus());
    for (let i = 0; i < 30 && document.activeElement !== stop; i += 1) {
      fireEvent.keyDown(document.activeElement, { key: "ArrowDown" });
    }
    expect(stop).toHaveFocus();

    // Nothing starts from it: Enter, Space or a click.
    fireEvent.keyDown(stop, { key: "Enter" });
    fireEvent.keyDown(stop, { key: " " });
    fireEvent.keyUp(stop, { key: " " });
    fireEvent.click(stop);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(server.requests).toEqual([]);
  });

  it("the status chip's Stop is held with the same words", async () => {
    await openList();
    const stop = within(row("#5")).getByRole("button", { name: "Stop run" });
    expect(stop).toBeDisabled();
    expect(stop).toHaveAccessibleDescription(SENTENCE);
    fireEvent.click(stop);
    expect(server.requests).toEqual([]);
  });
});

describe("Stop on the Experiments list with run control, or another refusal", () => {
  it("run control on: only the named run pauses, nothing is read, Undo resumes it", async () => {
    server.runControl = true;
    await openList();
    await openMenu("#5");
    fireEvent.click(screen.getByRole("menuitem", { name: "Stop" }));
    await confirmStop("#5");
    await waitFor(() => expect(server.toasts).toHaveLength(1));
    expect(server.requests).toEqual([named(RUN5)]);
    const said = server.toasts[0];
    expect(said.message).toBe("Stopped #5 · 4 of 40 rows kept");
    expect(said.options.variant).toBe("success");
    expect(typeof said.options.undo?.onUndo).toBe("function");
    expect(runControlState()).toBe("on");
  });

  it("any other refusal is shown and never becomes the stop with no body", async () => {
    server.otherError = {
      status: false,
      type: "not_found",
      code: "not_found",
      detail: "Run not found.",
      message: "Run not found.",
      statusCode: 404,
    };
    await openList();
    await openMenu("#5");
    fireEvent.click(screen.getByRole("menuitem", { name: "Stop" }));
    await confirmStop("#5");
    await waitFor(() => expect(server.toasts).toHaveLength(1));
    expect(server.toasts[0].options.variant).toBe("error");
    expect(server.toasts[0].message).toBe("Run not found.");
    // The run may still be going: said until it is closed.
    expect(server.toasts[0].options.persist).toBe(true);
    expect(server.requests).toEqual([named(RUN5)]);
    expect(runControlState()).toBe("unknown");
  });
});

describe("a Stop on the Experiments list that fails says so until it is closed", () => {
  it("the read at the click fails: nothing more is sent, and the page says why", async () => {
    server.readFails = true;
    await openList();
    await openMenu("#5");
    fireEvent.click(screen.getByRole("menuitem", { name: "Stop" }));
    await confirmStop("#5");
    await waitFor(() => expect(server.toasts).toHaveLength(1));
    expect(server.toasts[0].message).toBe(
      "Could not stop #5: the experiment's runs could not be read. Try again.",
    );
    expect(server.toasts[0].options).toMatchObject({
      variant: "error",
      persist: true,
    });
    expect(server.requests).toEqual([named(RUN5), read]);
    expect(wholeStops()).toEqual([]);
    // Nothing was stopped: the row still reads Running.
    expect(within(row("#5")).getByText("Running")).toBeInTheDocument();
  });

  it("the server refuses the stop with no body: the page says its words", async () => {
    server.wholeError = {
      status: false,
      type: "validation_error",
      code: "invalid",
      detail: "Experiment is not running. Current status: Completed",
      message: "Experiment is not running. Current status: Completed",
      statusCode: 400,
    };
    await openList();
    await openMenu("#5");
    fireEvent.click(screen.getByRole("menuitem", { name: "Stop" }));
    await confirmStop("#5");
    await waitFor(() => expect(server.toasts).toHaveLength(1));
    expect(server.toasts[0].message).toBe(
      "Experiment is not running. Current status: Completed",
    );
    expect(server.toasts[0].options).toMatchObject({
      variant: "error",
      persist: true,
    });
    expect(server.requests).toEqual([named(RUN5), read, wholeStop]);
  });

  it("a read of more than one page decides from every page: another run on page two stops nothing", async () => {
    server.experimentRuns = [
      run(RUN5, "#5", "Running"),
      run(RUN6, "#6", "Running"),
    ];
    server.runsPerPage = 1;
    await openList();
    await openMenu("#5");
    fireEvent.click(screen.getByRole("menuitem", { name: "Stop" }));
    await confirmStop("#5");
    await waitFor(() => expect(toastsWith(SENTENCE)).toHaveLength(1));
    expect(server.requests).toEqual([
      named(RUN5),
      read,
      { method: "get", url: "runs", offset: 0 },
      { method: "get", url: "runs", offset: 1 },
    ]);
    expect(wholeStops()).toEqual([]);
  });
});
