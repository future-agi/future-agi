// The classic experiment page runs several evaluations in ONE request. Run
// all and Run selected send one start_evals_process request carrying every
// id, so the run it starts never refuses the rest of the same press as a
// run that is still going. The per-row Run still sends its own id alone.
// The requests go through the real Axios instance, its request contract and
// its error normalization; only the transport adapter is replaced. Like the
// server, the stand-in answers the first evaluation request with a start
// and every later one with the running refusal.
import React from "react";
import { AxiosError } from "axios";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import client, { endpoints } from "src/utils/axios";

const DATASET = "7a2e3d4c-1111-4b6c-9dae-1f2a3b4c5d6e";
const EXPERIMENT = "7a2e3d4c-2222-4b6c-9dae-1f2a3b4c5d6e";
const QUALITY = "7a2e3d4c-3333-4b6c-9dae-1f2a3b4c5d6e";
const TONE = "7a2e3d4c-4444-4b6c-9dae-1f2a3b4c5d6e";
const LENGTH = "7a2e3d4c-5555-4b6c-9dae-1f2a3b4c5d6e";

const STILL_RUNNING =
  "This experiment is still running. Stop it, or wait for it to finish, then run evaluations again.";

const state = vi.hoisted(() => ({ snackbar: vi.fn(), evals: [] }));

vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
vi.mock("src/utils/Mixpanel", async () => ({
  ...(await import("src/utils/Mixpanel/EventNames")),
  resetUser: vi.fn(),
  trackEvent: vi.fn(),
}));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));
vi.mock("src/auth/context/jwt/utils", () => ({
  addToQueue: vi.fn(),
  clearTokens: vi.fn(),
  getIsRefreshing: vi.fn(),
  getRefreshToken: vi.fn(),
  getRememberMe: vi.fn(),
  processQueue: vi.fn(),
  refreshTokenRequest: vi.fn(),
  setIsRefreshing: vi.fn(),
  setSession: vi.fn(),
}));
// Every toast is recorded. No SnackbarProvider is mounted.
vi.mock("notistack", () => ({
  enqueueSnackbar: state.snackbar,
  useSnackbar: () => ({ enqueueSnackbar: state.snackbar }),
}));
vi.mock("../getEvalsList", async (original) => ({
  ...(await original()),
  useEvalsList: () => ({ data: { evals: state.evals }, isLoading: false }),
}));
vi.mock("src/sections/common/Runs/useRescore", () => ({
  useRescore: () => ({ request: vi.fn(), dialog: null }),
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

const RUN_URL = endpoints.develop.eval.runEvals(DATASET);
const originalAdapter = client.defaults.adapter;
const transport = vi.fn();

const evaluation = (id, name) => ({ id, name, status: "Completed" });

// The server's 400 envelope (tfc/utils/api_errors.py build_error_envelope).
const refused = (config, sentence) => {
  throw new AxiosError(
    "Request failed with status code 400",
    AxiosError.ERR_BAD_REQUEST,
    config,
    undefined,
    {
      status: 400,
      statusText: "Bad Request",
      config,
      headers: {},
      data: {
        status: false,
        type: "validation_error",
        code: "invalid",
        detail: sentence,
        message: sentence,
        error: sentence,
        result: sentence,
      },
    },
  );
};

const answered = (config, data) => ({
  status: 200,
  statusText: "OK",
  headers: {},
  config,
  data,
});

const clients = [];
beforeEach(() => {
  let started = false;
  transport.mockImplementation(async (config) => {
    if (config.method === "post" && config.url === RUN_URL) {
      // The first request starts the rerun and the experiment is running
      // from then on; any later request is refused, as the server does.
      if (started) return refused(config, STILL_RUNNING);
      started = true;
      return answered(config, {
        status: true,
        result: { message: "Eval rerun has started.", workflow_id: "wf-1" },
      });
    }
    return answered(config, { status: true, result: [] });
  });
  client.defaults.adapter = transport;
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((queryClient) => queryClient.clear());
  client.defaults.adapter = originalAdapter;
  setLaunchOverrideForTest(null);
  state.evals = [];
  vi.clearAllMocks();
});

const theme = () =>
  createTheme({ palette: palette("light"), customShadows: { drawer: "none" } });

function renderClassicDrawer(evals) {
  state.evals = evals;
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(queryClient);
  render(
    <ThemeProvider theme={theme()}>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/experiment/${EXPERIMENT}`]}>
          <Routes>
            <Route
              path="/experiment/:experimentId"
              element={
                <EvaluationDrawer
                  open
                  module="experiment"
                  id={DATASET}
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

const runCalls = () =>
  transport.mock.calls.filter(
    ([config]) => config.method === "post" && config.url === RUN_URL,
  );

const bodyOf = (call) => JSON.parse(call[0].data);

it("Run all (2) sends one request carrying both evaluations", async () => {
  renderClassicDrawer([
    evaluation(QUALITY, "quality"),
    evaluation(TONE, "tone"),
  ]);

  fireEvent.click(screen.getByRole("button", { name: "Run all (2)" }));

  await waitFor(() =>
    expect(state.snackbar).toHaveBeenCalledWith("quality and tone started", {
      variant: "success",
    }),
  );
  expect(runCalls()).toHaveLength(1);
  expect(bodyOf(runCalls()[0])).toEqual({
    user_eval_ids: [QUALITY, TONE],
    experiment_id: EXPERIMENT,
  });
  expect(state.snackbar).not.toHaveBeenCalledWith(
    STILL_RUNNING,
    expect.anything(),
  );
});

it("Run selected (2) sends one request carrying the two selected evaluations", async () => {
  renderClassicDrawer([
    evaluation(QUALITY, "quality"),
    evaluation(TONE, "tone"),
    evaluation(LENGTH, "length"),
  ]);
  // Each row's checkbox is named after its evaluation.
  fireEvent.click(screen.getByRole("checkbox", { name: "Select quality" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Select tone" }));

  fireEvent.click(screen.getByRole("button", { name: "Run selected (2)" }));

  await waitFor(() =>
    expect(state.snackbar).toHaveBeenCalledWith("quality and tone started", {
      variant: "success",
    }),
  );
  expect(runCalls()).toHaveLength(1);
  expect(bodyOf(runCalls()[0])).toEqual({
    user_eval_ids: [QUALITY, TONE],
    experiment_id: EXPERIMENT,
  });
  expect(state.snackbar).not.toHaveBeenCalledWith(
    STILL_RUNNING,
    expect.anything(),
  );
});

it("a row's Run still sends one request for its own evaluation", async () => {
  renderClassicDrawer([
    evaluation(QUALITY, "quality"),
    evaluation(TONE, "tone"),
  ]);

  fireEvent.click(screen.getAllByRole("button", { name: "Re-run" })[1]);
  const confirm = screen.getByText(
    "Re-run? This will overwrite previous results.",
  ).parentElement;
  fireEvent.click(within(confirm).getByRole("button", { name: "Re-run" }));

  await waitFor(() =>
    expect(state.snackbar).toHaveBeenCalledWith("tone started", {
      variant: "success",
    }),
  );
  expect(runCalls()).toHaveLength(1);
  expect(bodyOf(runCalls()[0])).toEqual({
    user_eval_ids: [TONE],
    experiment_id: EXPERIMENT,
  });
});

it("a refusal of the one request shows the server's sentence", async () => {
  transport.mockImplementation(async (config) => {
    if (config.method === "post" && config.url === RUN_URL) {
      return refused(config, STILL_RUNNING);
    }
    return answered(config, { status: true, result: [] });
  });
  renderClassicDrawer([
    evaluation(QUALITY, "quality"),
    evaluation(TONE, "tone"),
  ]);

  fireEvent.click(screen.getByRole("button", { name: "Run all (2)" }));

  await waitFor(() =>
    expect(state.snackbar).toHaveBeenCalledWith(STILL_RUNNING, {
      variant: "error",
    }),
  );
  expect(runCalls()).toHaveLength(1);
  expect(state.snackbar).toHaveBeenCalledTimes(1);
});
