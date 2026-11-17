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

const state = vi.hoisted(() => ({
  post: vi.fn(),
  rescore: vi.fn(),
  snackbar: vi.fn(),
}));
// No SnackbarProvider is mounted in this fixture. The named export is used
// after the request resolves, so wait for it below rather than leak a rejection.
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
vi.mock("src/sections/common/EvalPicker", () => ({
  EvalPickerDrawer: () => null,
}));
import EvaluationDrawer from "../EvaluationDrawer";
import { endpoints } from "src/utils/axios";

const clients = [];
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
  vi.clearAllMocks();
});

it.each([false, true])(
  "classic Run all uses the existing direct request, never W1-2 (experimentsBasic=%s)",
  async (experimentsBasic) => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic,
      madeReal: madeRealSurfaceIds(),
    });
    state.post.mockResolvedValue({ data: { status: true } });
    state.rescore.mockResolvedValue({ cancelled: true });
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
    expect(screen.queryByTestId("new-rescore-dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Run all (1)" }));
    await waitFor(() =>
      expect(state.post).toHaveBeenCalledWith(
        endpoints.develop.eval.runEvals("dataset-1"),
        { user_eval_ids: ["eval-1"], experiment_id: "exp-1" },
      ),
    );
    await waitFor(() =>
      expect(state.snackbar).toHaveBeenCalledWith("quality started", {
        variant: "success",
      }),
    );
    expect(state.snackbar).not.toHaveBeenCalledWith(expect.anything(), {
      variant: "error",
    });
    expect(state.rescore).not.toHaveBeenCalled();
    expect(state.post.mock.calls.some(([, body]) => body?.dry_run)).toBe(false);
  },
);
