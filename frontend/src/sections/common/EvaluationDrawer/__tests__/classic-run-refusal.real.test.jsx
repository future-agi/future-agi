// The classic experiment page's Run evaluations shows the server's refusal
// sentence. The request goes through the real Axios instance, its request
// contract and its error normalization, which rejects with the server's
// body at its top level; only the transport adapter is replaced, and no
// request leaves the test. "Failed to run evaluation" is shown only when
// the server gives no sentence a person can read.
import React from "react";
import { AxiosError } from "axios";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
  useMutation,
} from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import client, { endpoints } from "src/utils/axios";
import { handleError } from "src/utils/queryErrorHandler";

const DATASET = "6f1d2c3b-1111-4a5b-8c9d-0e1f2a3b4c5d";
const EXPERIMENT = "6f1d2c3b-2222-4a5b-8c9d-0e1f2a3b4c5d";
const EVALUATION = "6f1d2c3b-3333-4a5b-8c9d-0e1f2a3b4c5d";
const COLUMN = "6f1d2c3b-4444-4a5b-8c9d-0e1f2a3b4c5d";
const ROW = "6f1d2c3b-5555-4a5b-8c9d-0e1f2a3b4c5d";

const HTTP_REFUSAL =
  "Nothing was started. This experiment calls an HTTP endpoint, so a row is scored only when it is sent again. Retry failed rows from the Data tab or a failed output cell; to score every row, create a new experiment.";
const STILL_RUNNING =
  "This experiment is still running. Stop it, or wait for it to finish, then run evaluations again.";
const FALLBACK = "Failed to run evaluation";

const state = vi.hoisted(() => ({ snackbar: vi.fn() }));

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
// Every toast, the drawer's and the app's mutation error handler's, is
// recorded. No SnackbarProvider is mounted.
vi.mock("notistack", () => ({
  enqueueSnackbar: state.snackbar,
  useSnackbar: () => ({ enqueueSnackbar: state.snackbar }),
}));
vi.mock("../getEvalsList", async (original) => ({
  ...(await original()),
  useEvalsList: () => ({
    data: {
      evals: [{ id: EVALUATION, name: "quality", status: "Completed" }],
    },
    isLoading: false,
  }),
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
const CELL_URL = endpoints.develop.experiment.reRunExperimentCell(EXPERIMENT);
const originalAdapter = client.defaults.adapter;
const transport = vi.fn();
let refusal = HTTP_REFUSAL;

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

const clients = [];
beforeEach(() => {
  transport.mockImplementation(async (config) => {
    if (config.method === "post" && [RUN_URL, CELL_URL].includes(config.url)) {
      return refused(config, refusal);
    }
    return {
      status: 200,
      statusText: "OK",
      headers: {},
      config,
      data: { status: true, result: [] },
    };
  });
  client.defaults.adapter = transport;
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((queryClient) => queryClient.clear());
  client.defaults.adapter = originalAdapter;
  setLaunchOverrideForTest(null);
  refusal = HTTP_REFUSAL;
  vi.clearAllMocks();
});

const theme = () =>
  createTheme({ palette: palette("light"), customShadows: { drawer: "none" } });

function renderClassicDrawer() {
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

it.each([
  ["an HTTP endpoint's outputs", HTTP_REFUSAL],
  ["a run that is still going", STILL_RUNNING],
])(
  "Run evaluations refused for %s shows the server's sentence",
  async (_case, sentence) => {
    refusal = sentence;
    renderClassicDrawer();

    fireEvent.click(screen.getByRole("button", { name: "Run all (1)" }));

    await waitFor(() =>
      expect(state.snackbar).toHaveBeenCalledWith(sentence, {
        variant: "error",
      }),
    );
    expect(runCalls()).toHaveLength(1);
    expect(JSON.parse(runCalls()[0][0].data)).toEqual({
      user_eval_ids: [EVALUATION],
      experiment_id: EXPERIMENT,
    });
    expect(state.snackbar).not.toHaveBeenCalledWith(
      FALLBACK,
      expect.anything(),
    );
  },
);

// The classic grid's retry on a failed evaluation cell has no catch of its
// own: the app's mutation error handler shows what the server said.
function CellRetry() {
  const retry = useMutation({
    mutationFn: () =>
      client.post(CELL_URL, {
        cells: [{ column_id: COLUMN, row_id: ROW }],
        failed_only: true,
      }),
  });
  return (
    <button type="button" onClick={() => retry.mutate()}>
      Retry cell
    </button>
  );
}

it("a refused evaluation-cell retry shows the server's sentence", async () => {
  const queryClient = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  clients.push(queryClient);
  render(
    <QueryClientProvider client={queryClient}>
      <CellRetry />
    </QueryClientProvider>,
  );

  fireEvent.click(screen.getByRole("button", { name: "Retry cell" }));

  await waitFor(() =>
    expect(state.snackbar).toHaveBeenCalledWith(HTTP_REFUSAL, {
      variant: "error",
    }),
  );
  expect(state.snackbar).not.toHaveBeenCalledWith(FALLBACK, expect.anything());
});
