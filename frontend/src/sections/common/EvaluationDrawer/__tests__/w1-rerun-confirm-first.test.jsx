// W1 walk fix (row 11, finding 5): on an experiment's evaluations, Re-run
// opened "Re-score with …?" and started the rerun at once. The confirm's
// "count" was the real start_evals_process POST with dry_run, and the
// server hands an experiment's evals to rerun-cells without it: each press
// added a run, the dialog waited for a plan that never came, and Cancel and
// Escape were off. Now nothing is sent until Re-score, Escape closes the
// confirm, and two quick presses send one rerun.
import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";

const state = vi.hoisted(() => ({
  post: vi.fn(),
  notify: vi.fn(),
  snackbar: vi.fn(),
  closeSnackbar: vi.fn(),
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: state.snackbar,
  closeSnackbar: state.closeSnackbar,
  useSnackbar: () => ({
    enqueueSnackbar: state.snackbar,
    closeSnackbar: state.closeSnackbar,
  }),
}));
vi.mock("src/sections/common/ux/toast", async (original) => ({
  ...(await original()),
  notify: state.notify,
}));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: {
    post: state.post,
    get: vi.fn().mockResolvedValue({ data: { result: [] } }),
  },
}));
vi.mock("../getEvalsList", async (original) => ({
  ...(await original()),
  useEvalsList: () => ({
    data: { evals: [{ id: "eval-1", name: "quality", status: "Completed" }] },
    isLoading: false,
  }),
}));
vi.mock("../EvaluationsSelectionGrid", () => ({ default: () => null }));
vi.mock("../EvaluationMappingForm", () => ({ default: () => null }));
vi.mock("../CustomEvalsForm", () => ({ default: () => null }));
vi.mock("../CreateEvaluationGroupDrawer", () => ({ default: () => null }));
vi.mock("../DeleteEval", () => ({ default: () => null }));
vi.mock("../RunEvals", () => ({ default: () => null }));
vi.mock("src/sections/common/EvalPicker", () => ({
  EvalPickerDrawer: () => null,
}));
import EvaluationDrawer from "../EvaluationDrawer";
import { endpoints } from "src/utils/axios";

// What the server answers today: start_evals_process with an experiment
// hands the evals to rerun-cells, which starts the rerun.
const STARTED = {
  data: {
    status: true,
    result: { message: "Cell rerun has started.", workflow_id: "wf-1" },
  },
};
const RUN_URL = endpoints.develop.eval.runEvals("dataset-1");
const TITLE = "Re-score with quality?";
// Every request sent to the rerun door (the old "count" went there too).
const rerunsSent = () =>
  state.post.mock.calls.filter(([url]) => url === RUN_URL);

const clients = [];
afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
  vi.clearAllMocks();
});

function renderDrawer() {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
  state.post.mockResolvedValue(STARTED);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  render(
    <ThemeProvider
      theme={createTheme({
        palette: palette("light"),
        customShadows: { drawer: "none" },
      })}
    >
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/experiment/exp-1"]}>
          <Routes>
            <Route
              path="/experiment/:experimentId"
              element={
                <EvaluationDrawer
                  open
                  module="experiment"
                  id="dataset-1"
                  allColumns={[]}
                  onClose={() => {}}
                  refreshGrid={() => {}}
                />
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

it("Re-run sends nothing until Re-score, and two quick presses send one rerun", async () => {
  renderDrawer();
  const rerun = screen.getByRole("button", { name: "Re-run" });
  fireEvent.click(rerun);
  fireEvent.click(rerun);

  const dialog = await screen.findByRole("dialog", { name: TITLE });
  await act(async () => {});
  // The confirm comes first: no rerun (and no "count" that starts one).
  expect(rerunsSent()).toHaveLength(0);
  expect(
    within(dialog).getByText("Nothing is sent until you press Re-score."),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText(
      "Judge calls can't be counted before this starts.",
    ),
  ).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeEnabled();
  const go = within(dialog).getByRole("button", { name: "Re-score" });
  expect(go).toBeEnabled();

  // Both presses land before React re-renders the button as busy.
  act(() => {
    go.click();
    go.click();
  });

  await waitFor(() =>
    expect(state.notify).toHaveBeenCalledWith(
      "quality started",
      expect.objectContaining({ variant: "success" }),
    ),
  );
  expect(rerunsSent()).toHaveLength(1);
  expect(rerunsSent()[0][1]).toEqual({
    user_eval_ids: ["eval-1"],
    experiment_id: "exp-1",
  });
});

it("Escape closes the Re-run confirm and nothing is sent", async () => {
  renderDrawer();
  fireEvent.click(screen.getByRole("button", { name: "Re-run" }));
  const dialog = await screen.findByRole("dialog", { name: TITLE });

  fireEvent.keyDown(dialog, { key: "Escape" });

  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: TITLE }),
    ).not.toBeInTheDocument(),
  );
  expect(rerunsSent()).toHaveLength(0);
  expect(state.notify).not.toHaveBeenCalled();
});

it("Cancel closes the Re-run confirm and nothing is sent", async () => {
  renderDrawer();
  fireEvent.click(screen.getByRole("button", { name: "Re-run" }));
  const dialog = await screen.findByRole("dialog", { name: TITLE });

  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: TITLE }),
    ).not.toBeInTheDocument(),
  );
  expect(rerunsSent()).toHaveLength(0);
});
