import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";

// The classic experiment page saves an edited evaluation with one direct
// request. Whatever the server answers (queued, saved without scoring,
// refused with nothing saved) is shown to the person.

const state = vi.hoisted(() => ({
  post: vi.fn(),
  rescore: vi.fn(),
  snackbar: vi.fn(),
  edited: {
    userEvalId: "eval-1",
    name: "quality",
    bindingPayload: { config: { mapping: {} } },
  },
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: state.snackbar,
  useSnackbar: () => ({ enqueueSnackbar: state.snackbar }),
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
vi.mock("src/sections/common/Runs/useRescore", () => ({
  useRescore: () => ({
    request: state.rescore,
    dialog: <div data-testid="new-rescore-dialog" />,
  }),
}));
vi.mock("../EvaluationsSelectionGrid", () => ({ default: () => null }));
vi.mock("../EvaluationMappingForm", () => ({ default: () => null }));
vi.mock("../CustomEvalsForm", () => ({ default: () => null }));
vi.mock("../CreateEvaluationGroupDrawer", () => ({ default: () => null }));
vi.mock("../DeleteEval", () => ({ default: () => null }));
vi.mock("../RunEvals", () => ({ default: () => null }));
// The picker hands the drawer an edited evaluation, as its Update does.
vi.mock("src/sections/common/EvalPicker", () => ({
  // eslint-disable-next-line react/prop-types
  EvalPickerDrawer: ({ onEvalAdded }) => (
    <button
      type="button"
      onClick={() => {
        Promise.resolve(onEvalAdded?.(state.edited)).catch(() => {});
      }}
    >
      Update edited evaluation
    </button>
  ),
}));
import EvaluationDrawer from "../EvaluationDrawer";
import { endpoints } from "src/utils/axios";

const HTTP_SAVED =
  "Saved. This experiment calls an HTTP endpoint, so a row is scored with this change only when it is sent again. Retry failed rows from the Data tab or a failed cell; to score every row, create a new experiment. The scores shown are from before your change.";
const QUEUED = "Column evaluation updated and queued for processing";
const RUN_GOING =
  "A run of this experiment is going. Update this evaluation when it finishes. Nothing was saved.";

const clients = [];
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
  vi.clearAllMocks();
});

function renderClassicDrawer() {
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
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
                  allowRescore={false}
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

it("shows the HTTP sentence, not the queued one, when an HTTP experiment's edit is saved", async () => {
  state.post.mockResolvedValue({ data: { status: true, result: HTTP_SAVED } });
  renderClassicDrawer();

  fireEvent.click(
    screen.getByRole("button", { name: "Update edited evaluation" }),
  );

  await waitFor(() =>
    expect(state.post).toHaveBeenCalledWith(
      endpoints.develop.eval.editEval("dataset-1", "eval-1"),
      expect.objectContaining({ run: true, experiment_id: "exp-1" }),
    ),
  );
  await waitFor(() =>
    expect(state.snackbar).toHaveBeenCalledWith(HTTP_SAVED, {
      variant: "info",
    }),
  );
  expect(state.snackbar).not.toHaveBeenCalledWith(QUEUED, expect.anything());
  expect(state.rescore).not.toHaveBeenCalled();
});

it("shows the queued sentence where the evaluation is queued", async () => {
  state.post.mockResolvedValue({ data: { status: true, result: QUEUED } });
  renderClassicDrawer();

  fireEvent.click(
    screen.getByRole("button", { name: "Update edited evaluation" }),
  );

  await waitFor(() =>
    expect(state.snackbar).toHaveBeenCalledWith(QUEUED, { variant: "info" }),
  );
});

it("shows the refusal when the classic page's update is refused", async () => {
  state.post.mockRejectedValue({ statusCode: 400, result: RUN_GOING });
  renderClassicDrawer();

  fireEvent.click(
    screen.getByRole("button", { name: "Update edited evaluation" }),
  );

  await waitFor(() =>
    expect(state.snackbar).toHaveBeenCalledWith(RUN_GOING, {
      variant: "error",
    }),
  );
  expect(state.rescore).not.toHaveBeenCalled();
});
