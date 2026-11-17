// A Stop the server refuses, from the dataset's Experiments list, said on the
// page with the app's real toasts. A refusal used to be one information toast
// with no persist, so it went away with every toast (the provider's 4 s, made
// short here), and the row kept reading Running with a Stop in its menu:
// read a few seconds after the click, the page said nothing. The refusal
// now stays on the page until it is closed, in the same words the list
// shows for that Stop.
//
// The request functions are a stand-in server: run control off (a named stop
// answers 404 run_control_not_enabled), two runs of the experiment going.
// Notistack's CJS entry adds enqueueSnackbar/closeSnackbar only when its
// provider mounts; live getters reach the mounted provider.
/* eslint-disable react/prop-types */
import React, { useCallback, useState } from "react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { closeSnackbar } from "notistack";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { SnackbarProvider } from "src/components/snackbar";
import { resetRunControlForTest } from "src/sections/common/ExperimentRunControl/runControlAvailability";
import RunActionConfirmHost from "src/sections/develop-detail/Experiment/RunActionConfirmHost";
import RunRowMenu from "../RunRowMenu";
import RunRowMenuHost from "../RunRowMenuHost";
import RunStatusCell from "../RunStatusCell";

vi.mock("notistack", async () => {
  const actual = await vi.importActual("notistack");
  const live = actual.default ?? actual;
  return {
    ...actual,
    get enqueueSnackbar() {
      return live.enqueueSnackbar;
    },
    get closeSnackbar() {
      return live.closeSnackbar;
    },
  };
});
vi.mock("src/components/settings", async (importOriginal) => ({
  ...(await importOriginal()),
  useSettingsContext: () => ({ themeDirection: "ltr" }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunConfigs: () => ({ data: { results: [] } }),
}));
vi.mock("../cellContext", () => ({ useCell: (props) => props }));

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const RUN5 = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e05";
const RUN6 = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e06";
const SENTENCE =
  "This run can't be stopped on its own while other runs of this experiment are going. Open the experiment to stop all of its runs.";
// Every toast's life here (the app's is 4 s), and well past it with the
// exit transition.
const TOAST_MS = 300;
const PAST_TOAST_MS = 1500;

const server = vi.hoisted(() => ({
  requests: [],
  runs: [],
  // The server's refusal of the stop with no body, and a failed read.
  wholeError: null,
  readFails: false,
}));
vi.mock("src/api/develop/experiment-run-actions", async (importOriginal) => ({
  ...(await importOriginal()),
  stopRuns: async (experimentId, runIds) => {
    const body = runIds === undefined ? {} : { run_ids: runIds };
    server.requests.push({ method: "post", body });
    if (body.run_ids) {
      throw {
        status: false,
        type: "not_found",
        code: "run_control_not_enabled",
        detail: "Run control is not available.",
        message: "Run control is not available.",
        statusCode: 404,
      };
    }
    if (server.wholeError) throw server.wholeError;
    return { status: 200, data: { status: true, result: {} } };
  },
}));

const run = (run_id, label) => ({
  run_id,
  experiment_id: EXP,
  experiment_ordinal: 1,
  experiment_name: "Ticket triage",
  label,
  name: "Prompt",
  target_type: "external",
  status: "Running",
  progress: { total: 40, done: 1, errors: 0 },
});

const experimentUrl = `/model-hub/experiments/v2/${EXP}/`;
const realGet = axios.get;
const theme = createTheme({ palette: { border: { default: "#dddddd" } } });

// The toasts on the page's toast column, and their text.
const toasts = () => [
  ...document.querySelectorAll(".fa-toasts .notistack-MuiContent"),
];
const toastText = () =>
  toasts()
    .map((el) => el.textContent)
    .join("\n");

function List() {
  const { data: runs = [] } = useQuery({
    queryKey: ["develop", "dataset-runs", "ds"],
    queryFn: async () => server.runs.map((r) => ({ ...r })),
  });
  const [menu, setMenu] = useState(null);
  const closeMenu = useCallback(() => setMenu(null), []);
  const context = {
    datasetId: "ds",
    canEdit: true,
    canRun: true,
    navigate: () => {},
    copyText: () => {},
    allRuns: runs,
    runsPerExperiment: { [EXP]: runs.length },
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

beforeEach(() => {
  window.innerWidth = 1600;
  resetRunControlForTest();
  server.requests.length = 0;
  server.wholeError = null;
  server.readFails = false;
  server.runs = [run(RUN5, "#5"), run(RUN6, "#6")];
  vi.spyOn(axios, "get").mockImplementation((url, config) => {
    if (url !== experimentUrl) return realGet.call(axios, url, config);
    server.requests.push({ method: "get" });
    if (server.readFails) return Promise.reject(new Error("Network Error"));
    const runs = server.runs.map((r) => ({ ...r }));
    return Promise.resolve({
      status: 200,
      data: {
        status: true,
        result: {
          id: EXP,
          dataset_id: "ds",
          runs,
          runs_total: runs.length,
          runs_has_more: false,
          runs_next: null,
        },
      },
    });
  });
});
afterEach(() => {
  act(() => {
    closeSnackbar();
  });
  cleanup();
  vi.restoreAllMocks();
});

describe("a refused Stop on the Experiments list, with the app's toasts", () => {
  it("says the sentence on the page and keeps it there after every toast's time", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <ThemeProvider theme={theme}>
        <SnackbarProvider autoHideDuration={TOAST_MS}>
          <QueryClientProvider client={qc}>
            <MemoryRouter>
              <List />
            </MemoryRouter>
          </QueryClientProvider>
        </SnackbarProvider>
      </ThemeProvider>,
    );
    await screen.findByTestId("row #5");
    fireEvent.click(screen.getByRole("button", { name: "#5 actions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Stop" }));
    const dialog = await screen.findByRole("dialog", { name: "Stop #5?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Stop run" }));

    // A message comes up at once. REFUSAL also matches the words this Stop
    // used to show, so on a build without the fix this test fails where a
    // person did: the message is gone when the page is read again.
    const REFUSAL =
      /can't be stopped on its own|Stop the whole experiment from its page/;
    await waitFor(() => expect(toastText()).toMatch(REFUSAL));

    // Read later, as a person looking back at the page does: still there.
    await act(() => new Promise((done) => setTimeout(done, PAST_TOAST_MS)));
    expect(toastText()).toMatch(REFUSAL);

    // In the list's words.
    expect(toastText()).toContain(SENTENCE);
    // The server refused the named stop, the read at the click shows a
    // second run going: nothing was stopped, and no stop with no body was
    // sent.
    expect(server.requests).toEqual([
      { method: "post", body: { run_ids: [RUN5] } },
      { method: "get" },
    ]);

    // It closes when the person closes it.
    const toast = toasts().find((el) => el.textContent.includes(SENTENCE));
    fireEvent.click(
      within(toast).getByRole("button", { name: "Dismiss notification" }),
    );
    await waitFor(() => expect(toastText()).not.toContain(SENTENCE));
  });
});

// Opens the list with the app's toasts and confirms #5's Stop.
async function stopFromList() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <ThemeProvider theme={theme}>
      <SnackbarProvider autoHideDuration={TOAST_MS}>
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <List />
          </MemoryRouter>
        </QueryClientProvider>
      </SnackbarProvider>
    </ThemeProvider>,
  );
  await screen.findByTestId("row #5");
  fireEvent.click(screen.getByRole("button", { name: "#5 actions" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Stop" }));
  const dialog = await screen.findByRole("dialog", { name: "Stop #5?" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Stop run" }));
}

// The message comes up, is still there after every toast's time, and goes
// only when the person closes it.
async function staysUntilClosed(message) {
  await waitFor(() => expect(toastText()).toContain(message));
  await act(() => new Promise((done) => setTimeout(done, PAST_TOAST_MS)));
  expect(toastText()).toContain(message);
  const toast = toasts().find((el) => el.textContent.includes(message));
  fireEvent.click(
    within(toast).getByRole("button", { name: "Dismiss notification" }),
  );
  await waitFor(() => expect(toastText()).not.toContain(message));
}

describe("a Stop on the Experiments list that fails, with the app's toasts", () => {
  it("the read at the click fails: the message stays on the page and nothing more is sent", async () => {
    server.readFails = true;
    await stopFromList();
    await staysUntilClosed(
      "Could not stop #5: the experiment's runs could not be read. Try again.",
    );
    expect(server.requests).toEqual([
      { method: "post", body: { run_ids: [RUN5] } },
      { method: "get" },
    ]);
  });

  it("the server refuses the stop with no body: its words stay on the page", async () => {
    // #5 is the experiment's one run going, so the stop with no body is sent.
    server.runs = [run(RUN5, "#5")];
    server.wholeError = {
      status: false,
      type: "validation_error",
      code: "invalid",
      detail: "Experiment is not running. Current status: Completed",
      message: "Experiment is not running. Current status: Completed",
      statusCode: 400,
    };
    await stopFromList();
    await staysUntilClosed(
      "Experiment is not running. Current status: Completed",
    );
    expect(server.requests).toEqual([
      { method: "post", body: { run_ids: [RUN5] } },
      { method: "get" },
      { method: "post", body: {} },
    ]);
  });
});
