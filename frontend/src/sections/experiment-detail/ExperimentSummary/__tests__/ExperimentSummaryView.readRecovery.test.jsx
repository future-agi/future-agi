import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ExperimentSummaryView from "../ExperimentSummaryView";

const http = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: http,
  endpoints: {
    develop: {
      experiment: {
        getExperimentDetails: (id) => `/experiments/v2/${id}/`,
      },
    },
  },
}));

// Keep the detail hook, query state, router and MUI controls real. Only the
// downstream analysis is a probe, so it cannot fetch or invent run data.
/* eslint-disable react/prop-types */
vi.mock("../Analysis/ExperimentAnalysisView", () => ({
  default: ({ datasetId, runIds, baselineId, mode }) => (
    <div data-testid="analysis">
      {JSON.stringify({ datasetId, runIds, baselineId, mode })}
    </div>
  ),
}));
/* eslint-enable react/prop-types */

const clients = [];
const detail = {
  experiment_id: "experiment-a",
  dataset_id: "dataset-a",
  runs: [{ run_id: "run-a" }, { run_id: "run-b" }],
  runs_total: 2,
  runs_has_more: false,
};
const response = (value) => ({ data: { status: true, result: value } });
const unavailable = () => Object.assign(new Error("Service unavailable"), {
  response: { status: 503 },
});
const analysisProps = () => JSON.parse(screen.getByTestId("analysis").textContent);

function deferred() {
  let resolve;
  const promise = new Promise((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

function NextExperiment() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate("/experiment/experiment-b/summary")}>
      Next experiment
    </button>
  );
}

function renderSummary(search = "") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/experiment/experiment-a/summary${search}`]}>
        <NextExperiment />
        <Routes>
          <Route path="/experiment/:experimentId/summary" element={<ExperimentSummaryView />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

beforeEach(() => { http.get.mockReset(); });
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
});

describe("Experiment Summary detail-read recovery", () => {
  it("replaces a settled initial 503 with Retry and mounts analysis only after detail recovers", async () => {
    const failure = unavailable();
    const recovery = deferred();
    http.get.mockRejectedValueOnce(failure).mockReturnValueOnce(recovery.promise);
    const client = renderSummary("?baseline=run-b");

    expect(await screen.findByRole("alert")).toHaveTextContent(/experiment details could not be loaded/i);
    expect(client.getQueryState(["experiment", "experiment-a"])).toMatchObject({
      data: undefined, status: "error", fetchStatus: "idle", error: failure,
    });
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByTestId("analysis")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(http.get).toHaveBeenCalledTimes(2));
    expect(http.get).toHaveBeenNthCalledWith(2, "/experiments/v2/experiment-a/", {
      signal: expect.anything(),
    });
    expect(screen.queryByTestId("analysis")).not.toBeInTheDocument();
    await act(async () => { recovery.resolve(response(detail)); });

    await screen.findByTestId("analysis");
    expect(analysisProps()).toEqual({
      datasetId: "dataset-a", runIds: ["run-a", "run-b"],
      baselineId: "run-b", mode: "experiment",
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("keeps the progress bar for a genuinely pending detail read", async () => {
    const pending = deferred();
    http.get.mockReturnValueOnce(pending.promise);
    renderSummary();

    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("analysis")).not.toBeInTheDocument();

    await act(async () => {
      pending.resolve(response({ ...detail, runs: [{ run_id: "run-a" }], runs_total: 1 }));
    });
    await screen.findByTestId("analysis");
    expect(analysisProps()).toEqual({
      datasetId: "dataset-a", runIds: ["run-a"], baselineId: "none", mode: "experiment",
    });
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it.each([
    ["missing canonical runs", { runs: undefined }],
    ["missing catalog completeness", { runs_has_more: undefined }],
    ["incomplete canonical catalog", { runs_total: 3 }],
  ])("offers recovery for %s instead of passing fabricated empty runs", async (_name, invalid) => {
    http.get.mockResolvedValueOnce(response({ ...detail, ...invalid }))
      .mockResolvedValueOnce(response(detail));
    const client = renderSummary();

    expect(await screen.findByRole("alert")).toHaveTextContent(/experiment details could not be loaded/i);
    expect(client.getQueryState(["experiment", "experiment-a"])).toMatchObject({
      data: undefined, status: "error", fetchStatus: "idle",
    });
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByTestId("analysis")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByTestId("analysis");
    expect(http.get).toHaveBeenCalledTimes(2);
    expect(analysisProps()).toEqual({
      datasetId: "dataset-a", runIds: ["run-a", "run-b"], mode: "experiment",
    });
  });

  it("does not show cached analysis when a later detail read fails", async () => {
    http.get.mockResolvedValueOnce(response(detail)).mockRejectedValueOnce(unavailable())
      .mockResolvedValueOnce(response(detail));
    const client = renderSummary();
    await screen.findByTestId("analysis");

    await act(async () => {
      await client.refetchQueries({ queryKey: ["experiment", "experiment-a"], exact: true });
    });
    await screen.findByRole("alert");
    expect(screen.queryByTestId("analysis")).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByTestId("analysis");
    expect(http.get).toHaveBeenCalledTimes(3);
    expect(analysisProps().runIds).toEqual(["run-a", "run-b"]);
  });

  it("retries the current experiment without reusing the previous experiment's runs", async () => {
    const nextDetail = {
      experiment_id: "experiment-b", dataset_id: "dataset-b",
      runs: [{ run_id: "run-c" }], runs_total: 1, runs_has_more: false,
    };
    http.get.mockResolvedValueOnce(response(detail)).mockRejectedValueOnce(unavailable())
      .mockResolvedValueOnce(response(nextDetail));
    renderSummary();
    await screen.findByTestId("analysis");

    fireEvent.click(screen.getByRole("button", { name: "Next experiment" }));
    await screen.findByRole("alert");
    expect(screen.queryByTestId("analysis")).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByTestId("analysis");
    expect(http.get).toHaveBeenNthCalledWith(3, "/experiments/v2/experiment-b/", {
      signal: expect.anything(),
    });
    expect(analysisProps()).toEqual({
      datasetId: "dataset-b", runIds: ["run-c"], baselineId: "none", mode: "experiment",
    });
  });
});
