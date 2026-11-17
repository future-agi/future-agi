// W10-EXPERIMENTS-DATASETS-U07: the shared API client (src/utils/axios.js)
// rejects a failed read with { ...response body, statusCode, transportCode,
// responseHeaders } and keeps no `response`. The Summary must read that
// status, so each failure gets its own guidance (compare fewer runs, access),
// and a request that got no answer says so. The server's own message is never
// shown, and a malformed answer (a plain Error) keeps the general message.
import React from "react";
import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, renderWithRouter, screen, waitFor } from "src/utils/test-utils";
import { handleError } from "src/utils/queryErrorHandler";
import ExperimentAnalysisView from "../ExperimentAnalysisView";

const hooks = vi.hoisted(() => ({
  analysis: vi.fn(), stats: vi.fn(), get: vi.fn(), enqueueSnackbar: vi.fn(),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: hooks.get } };
});
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: hooks.enqueueSnackbar,
}));
vi.mock("src/api/develop/analysis", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunAnalysis: hooks.analysis,
}));
vi.mock("src/api/develop/experiment-stats", async (importOriginal) => ({
  ...(await importOriginal()),
  useExperimentStats: hooks.stats,
}));

const DATASET = "11111111-1111-4111-8111-111111111111";
const RUN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const RUN_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const RUN_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

// What the client rejects with for an HTTP error: the error envelope the
// server sent (tfc/utils/api_errors.py build_error_envelope), plus its status.
function httpFailure(statusCode, message) {
  return {
    status: false,
    type: "api_error",
    code: "error",
    detail: message,
    message,
    error: message,
    result: message,
    statusCode,
    transportCode: statusCode >= 500 ? "ERR_BAD_RESPONSE" : "ERR_BAD_REQUEST",
    responseHeaders: { "x-request-id": "request-1" },
  };
}

let refetch;
let queryClient;

function failWith(error) {
  hooks.analysis.mockReturnValue({
    data: undefined,
    isLoading: false,
    isFetching: false,
    isError: true,
    error,
    refetch,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  hooks.analysis.mockReset();
  hooks.get.mockReset();
  refetch = vi.fn();
  hooks.stats.mockReturnValue({
    data: {
      column_config: [{ name: "Accuracy" }],
      table_data: [],
      metadata: { is_winner_chosen: false },
    },
    isPending: false,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  queryClient?.clear();
  queryClient = undefined;
});

function renderSummary() {
  return renderWithRouter(
    <Routes>
      <Route
        path="/dashboard/develop/experiment/:experimentId/summary"
        element={
          <ExperimentAnalysisView
            datasetId={DATASET}
            runIds={[RUN_A, RUN_B]}
            baselineId={RUN_A}
            mode="experiment"
          />
        }
      />
    </Routes>,
    { route: "/dashboard/develop/experiment/E1/summary" },
  );
}

describe("W10-EXPERIMENTS-DATASETS-U07 Summary failure messages", () => {
  it.each([
    [503, "Complete analysis exceeds its work bound.",
      "The analysis is too large or temporarily unavailable. Retry, or compare fewer runs."],
    [403, "You do not have permission to perform this action.",
      "You do not have access to this analysis."],
    [404, "Experiment run not found",
      "This dataset or one of the selected runs is unavailable in this workspace."],
    [409, "Immutable run evidence is incomplete or incompatible with analysis.",
      "The recorded results for these runs are incomplete or cannot be compared."],
    [400, "Run must be in the selected run set.",
      "The baseline, candidate, metric or filters in this link are not supported. Check the link."],
  ])("explains a %i from the API client in its own words", (statusCode, serverMessage, shown) => {
    failWith(httpFailure(statusCode, serverMessage));
    renderSummary();

    expect(screen.getByText(shown)).toBeVisible();
    expect(screen.queryByText("The analysis could not be loaded.")).toBeNull();
    expect(document.body).not.toHaveTextContent(serverMessage);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("says the analysis could not be reached when no response arrived", () => {
    failWith({
      message: "Network Error",
      statusCode: undefined,
      transportCode: "ERR_NETWORK",
      responseHeaders: { "x-request-id": undefined },
    });
    renderSummary();

    expect(
      screen.getByText("The analysis could not be reached. Check your connection, then retry."),
    ).toBeVisible();
    expect(document.body).not.toHaveTextContent("Network Error");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("keeps the general message for an unlisted status", () => {
    failWith(httpFailure(500, "Internal server error."));
    renderSummary();

    expect(screen.getByText("The analysis could not be loaded.")).toBeVisible();
    expect(screen.queryByText(/could not be reached/)).toBeNull();
  });

  it("keeps the general message for a malformed answer", () => {
    failWith(new Error("Invalid analysis response: runs.run_id"));
    renderSummary();

    expect(screen.getByText("The analysis could not be loaded.")).toBeVisible();
    expect(document.body).not.toHaveTextContent("Invalid analysis response");
  });

  it("shows the real 409 alert without a snackbar and marks the candidate fallback as handled", async () => {
    const { useRunAnalysis, useCandidateRunId, analysisKey } = await vi.importActual(
      "src/api/develop/analysis",
    );
    const runIds = [RUN_A, RUN_B, RUN_C];
    const params = { runs: runIds.join(","), baseline: RUN_A };
    const serverMessage = "Immutable run evidence is incomplete or incompatible with analysis.";
    const shown = "The recorded results for these runs are incomplete or cannot be compared.";
    hooks.analysis.mockImplementation(useRunAnalysis);
    hooks.get.mockRejectedValue(httpFailure(409, serverMessage));
    queryClient = new QueryClient({
      queryCache: new QueryCache({ onError: handleError }),
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });

    // Three runs trigger the wrapper's separate, silent candidate analysis read.
    function CandidateProbe() {
      const candidateId = useCandidateRunId(DATASET, {
        runIds, baselineId: RUN_A, analysisBaselineId: RUN_A,
      });
      return <output aria-label="Fallback candidate">{candidateId}</output>;
    }

    renderWithRouter(
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route
            path="/dashboard/develop/experiment/:experimentId/summary"
            element={
              <>
                <ExperimentAnalysisView
                  datasetId={DATASET}
                  runIds={runIds}
                  baselineId={RUN_A}
                  mode="experiment"
                />
                <CandidateProbe />
              </>
            }
          />
        </Routes>
      </QueryClientProvider>,
      { route: "/dashboard/develop/experiment/E1/summary" },
    );

    expect(await screen.findByText(shown)).toBeVisible();
    expect(screen.getByText(shown).closest('[role="alert"]')).toHaveTextContent(shown);
    const summaryQuery = queryClient.getQueryCache().find({
      queryKey: [...analysisKey(DATASET), { ...params, include: "root_causes" }],
      exact: true,
    });
    const candidateQuery = queryClient.getQueryCache().find({
      queryKey: [...analysisKey(DATASET), params],
      exact: true,
    });
    await waitFor(() => {
      expect(queryClient.isFetching()).toBe(0);
      expect(summaryQuery?.state.status).toBe("error");
      expect(candidateQuery?.state.status).toBe("error");
    });
    expect(hooks.get).toHaveBeenCalledTimes(2);
    expect(hooks.get).toHaveBeenCalledWith(expect.any(String), { params });
    expect(hooks.get).toHaveBeenCalledWith(expect.any(String), {
      params: { ...params, include: "root_causes" },
    });
    expect(summaryQuery.meta.errorHandled).toBe(true);
    expect(candidateQuery.meta.errorHandled).toBe(true);
    expect(screen.getByLabelText("Fallback candidate")).toHaveTextContent(RUN_B);
    expect(document.body).not.toHaveTextContent(serverMessage);
    expect(hooks.enqueueSnackbar).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => {
      expect(hooks.get).toHaveBeenCalledTimes(3);
      expect(queryClient.isFetching()).toBe(0);
      expect(summaryQuery.state.status).toBe("error");
    });
    expect(await screen.findByText(shown)).toBeVisible();
    expect(hooks.enqueueSnackbar).not.toHaveBeenCalled();
  });
});
