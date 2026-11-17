import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { datasetRunsKey } from "src/api/develop/runs";
import { LeaderboardShell } from "../LeaderboardStates";
import { useLeaderboardView } from "../useLeaderboard";

vi.mock("../useLeaderboardActions", () => ({
  usePendingDeletes: (select) => select({ byDataset: {} }),
}));
// Keep the real shell and empty-state decision; isolate unrelated create UI.
vi.mock("../useOpenSource", () => ({ default: () => vi.fn() }));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: "rows", dataset: { row_count: 2 } }),
}));
vi.mock("src/sections/common/RunSources/useRunSourceAvailability", () => ({
  useRunSourceAvailability: () => null,
}));
vi.mock("src/sections/common/RunSources/SourceCards", () => ({
  default: () => null,
}));

const DATASET = "10000000-0000-4000-8000-000000000001";
const RUN = {
  run_id: "30000000-0000-4000-8000-000000000001",
  label: "#1",
  name: "Existing run",
  status: "completed",
  listed: true,
  final: true,
  target_type: "prompt",
  dataset_version: { id: "20000000-0000-4000-8000-000000000001" },
  progress: { done: 2, scored: 2, total: 2, errors: 0 },
  metrics: {},
};
const response = {
  data: {
    status: true,
    result: {
      results: [RUN],
      count: 1,
      offset: 0,
      has_more: false,
      next: null,
      pinned_run_id: null,
      baseline_run_id: null,
      baseline_run: null,
      metric_defs: [],
    },
  },
};
let client;
let get;
let view;

function History() {
  view = useLeaderboardView();
  return (
    <>
      <button onClick={() => view.query.refetch()}>Refresh history</button>
      <LeaderboardShell
        view={view}
        onClearFilters={() => {}}
        grid={
          <output data-testid="history-runs">
            {view.runs.map((run) => run.name).join(",")}
          </output>
        }
      />
    </>
  );
}

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  get = vi.spyOn(axios, "get").mockResolvedValue(response);
  window.history.replaceState({}, "", `/history/${DATASET}`);
});
afterEach(() => {
  cleanup();
  client.clear();
  vi.restoreAllMocks();
});

describe("History refresh", () => {
  it("shows the read error and Retry instead of an empty dataset after a failed refresh", async () => {
    render(
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="/history/:dataset" element={<History />} />
        </Routes>
      </QueryClientProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("history-runs").textContent).toBe(
        "Existing run",
      ),
    );
    expect(view.version).toBe("all");
    get.mockRejectedValueOnce(new Error("History refresh failed."));
    fireEvent.click(screen.getByRole("button", { name: "Refresh history" }));
    await waitFor(() => expect(view.query.isRefetchError).toBe(true));

    // React Query retains the successful transport response on refetch error.
    expect(
      client.getQueryData([
        ...datasetRunsKey(DATASET),
        { include: "metrics,config", limit: 500 },
      ]).data.result.results,
    ).toEqual([RUN]);
    expect(view.query.data).toBeUndefined();
    expect(view.allRuns).toEqual([]);
    expect(screen.getByText("Couldn't load runs.").textContent).toBe(
      "Couldn't load runs.",
    );
    expect(screen.getByText("History refresh failed.").textContent).toBe(
      "History refresh failed.",
    );
    expect(screen.queryByTestId("experiments-empty")).toBeNull();
    expect(screen.queryByTestId("history-runs")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^Retry/ }));
    await waitFor(() =>
      expect(screen.getByTestId("history-runs").textContent).toBe(
        "Existing run",
      ),
    );
    expect(view.query.isError).toBe(false);
    expect(view.query.data.results.map((run) => run.run_id)).toEqual([
      RUN.run_id,
    ]);
    expect(screen.queryByTestId("error-state")).toBeNull();
    expect(screen.queryByTestId("experiments-empty")).toBeNull();
    expect(get).toHaveBeenCalledTimes(3);
  });
});
