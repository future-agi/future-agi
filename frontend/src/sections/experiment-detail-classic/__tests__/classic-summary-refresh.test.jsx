import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { renderWithRouter } from "src/utils/test-utils";
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";
import { Route, Routes, useNavigate } from "react-router-dom";

const requests = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: requests,
  endpoints: {
    develop: {
      experiment: {
        getExperimentDetails: (id) => `/model-hub/experiments/v2/${id}/`,
        getSummary: (id) => `/model-hub/experiments/v2/${id}/stats/`,
      },
    },
  },
}));
vi.mock("src/utils/utils", () => ({
  getUniqueColorPalette: () => ({ solid: "#336699", tagForeground: "#6699cc" }),
}));
// Keep the real Summary host, queries and polling policy; replace only the
// presentation boundary so assertions read the rows delivered to the grid.
/* eslint-disable react/prop-types */
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/ExperimentSummaryTable",
  () => ({
    default: ({ rows }) => (
      <table aria-label="Experiment summary">
        <thead>
          <tr>
            <th>Variant</th>
            <th>Quality</th>
            <th>Prompt tokens</th>
            <th>Completion tokens</th>
            <th>Total tokens</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.dataset_id}>
              <td>{row.experiment_dataset_name}</td>
              <td>{row.quality}</td>
              <td>{row.prompt_token}</td>
              <td>{row.completion_token}</td>
              <td>{row.total_token}</td>
            </tr>
          ))}
        </tbody>
      </table>
    ),
  }),
);
/* eslint-enable react/prop-types */
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/ChooseWinnerDrawer",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/ExperimentEvaluationChart",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/ExperimentSummaryChart/SpiderChartExperiment",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/tableConfig",
  () => ({ ExperimentSummaryStaticColumnDefs: [] }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/CustomRenderers/ExperimentEvalCellRenderer",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/CustomRenderers/RankWithIndexRenderer",
  () => ({ default: () => null }),
);

import ClassicExperimentSummary from "../ClassicExperimentSummary";

const experimentId = "experiment-a";
const otherId = "experiment-b";
const detailUrl = (id = experimentId) => `/model-hub/experiments/v2/${id}/`;
const statsUrl = (id = experimentId) => `${detailUrl(id)}stats/`;
const detailRead = (status) => ({
  data: {
    status: true,
    result: {
      status,
      runs: [],
      runs_total: 0,
      runs_has_more: false,
      runs_next: null,
    },
  },
});
const rowsWith = (values) => values.map(([quality, prompt, completion], index) => ({
  dataset_id: `variant-${index}`,
  experiment_dataset_name: `Variant ${index + 1}`,
  quality,
  prompt_token: prompt,
  completion_token: completion,
  total_token: prompt + completion,
}));
const partialRows = rowsWith([[25, 10, 5], [40, 20, 10]]);
const progressRows = rowsWith([[50, 30, 15], [60, 40, 20]]);
const finalRows = rowsWith([[95, 300, 100], [85, 350, 150]]);
const statsRead = (rows) => ({
  data: {
    status: true,
    result: {
      metadata: {},
      column_config: [{ id: "quality", name: "quality" }],
      table_data: rows,
    },
  },
});
const callsTo = (url) => requests.get.mock.calls.filter(([requestUrl]) => requestUrl === url);
const flush = async (ms = 20) => {
  await act(async () => vi.advanceTimersByTimeAsync(ms));
  // The status update commits at the end of the first act. Its effect starts
  // the summary read, whose queued notification needs a separate timer turn
  // and React commit before we inspect the rows.
  await act(async () => vi.advanceTimersByTimeAsync(20));
};
const clients = [];

function RouteSwitch() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate(`/experiment/${otherId}/summary`)}>
      Open second experiment
    </button>
  );
}

function mountSummary() {
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5000,
        gcTime: 60000,
        retry: false,
        refetchOnMount: true,
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
      },
      mutations: { retry: false },
    },
  });
  clients.push(client);
  renderWithRouter(
    <QueryClientProvider client={client}>
      <RouteSwitch />
      <Routes>
        <Route
          path="/experiment/:experimentId/summary"
          element={<ClassicExperimentSummary />}
        />
      </Routes>
    </QueryClientProvider>,
    { route: `/experiment/${experimentId}/summary` },
  );
  return client;
}

function expectRows(expected) {
  const rows = within(screen.getByRole("table", { name: "Experiment summary" }))
    .getAllByRole("row").slice(1);
  expect(rows.map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent)))
    .toEqual(expected.map((row) => [
      row.experiment_dataset_name,
      row.quality,
      row.prompt_token,
      row.completion_token,
      row.total_token,
    ].map(String)));
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  focusManager.setFocused(true);
});
afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
  clients.splice(0).forEach((client) => client.clear());
  focusManager.setFocused(undefined);
  vi.useRealTimers();
});

describe("classic summary refresh", () => {
  it("refreshes both variants while running and on completion without external triggers", async () => {
    let detailReads = 0;
    requests.get.mockImplementation(async (url) => {
      if (url === detailUrl()) {
        detailReads += 1;
        return detailRead(detailReads < 3 ? "Running" : "Completed");
      }
      if (url === statsUrl()) {
        return statsRead(detailReads >= 3 ? finalRows : detailReads >= 2 ? progressRows : partialRows);
      }
      throw new Error(`Unexpected GET: ${url}`);
    });
    const client = mountSummary();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const nestedKey = ["experiment-summary", experimentId, "unrelated"];
    const otherKey = ["experiment-summary", otherId];
    client.setQueryData(nestedKey, { untouched: true });
    client.setQueryData(otherKey, { untouched: true });
    await flush();
    expectRows(partialRows);
    const initialStatsReads = callsTo(statsUrl()).length;
    expect(screen.queryByRole("button", { name: "Re-run with changes" })).toBeNull();

    // No navigation, focus/reconnect event, mutation, or manual refetch occurs.
    await flush(2000);
    expectRows(progressRows);
    expect(callsTo(statsUrl()).length).toBeGreaterThan(initialStatsReads);
    const pendingStatsReads = callsTo(statsUrl()).length;
    await flush(4000);
    expectRows(finalRows);
    expect(callsTo(detailUrl())).toHaveLength(3);
    expect(callsTo(statsUrl()).length).toBeGreaterThan(pendingStatsReads);
    expect(invalidate).toHaveBeenCalled();
    for (const [filters] of invalidate.mock.calls) {
      expect(filters).toEqual({ queryKey: ["experiment-summary", experimentId], exact: true });
    }
    expect(client.getQueryState(nestedKey).isInvalidated).toBe(false);
    expect(client.getQueryState(otherKey).isInvalidated).toBe(false);
    expect(requests.post).not.toHaveBeenCalled();
    expect(focusManager.isFocused()).toBe(true);
    const readsAfterCompletion = requests.get.mock.calls.length;
    await flush(600000);
    expect(requests.get).toHaveBeenCalledTimes(readsAfterCompletion);
    expectRows(finalRows);
  });

  it("fetches final stats even when the initial partial stats read is still in flight", async () => {
    let detailReads = 0;
    let initialSignal;
    let resolveInitial;
    const initialStats = new Promise((resolve) => { resolveInitial = resolve; });
    requests.get.mockImplementation((url, options) => {
      if (url === detailUrl()) {
        detailReads += 1;
        return Promise.resolve(detailRead(detailReads === 1 ? "Running" : "Completed"));
      }
      if (url === statsUrl()) {
        if (detailReads < 2) {
          initialSignal = options?.signal;
          return initialStats;
        }
        return Promise.resolve(statsRead(finalRows));
      }
      throw new Error(`Unexpected GET: ${url}`);
    });
    mountSummary();
    await flush();
    await flush(2000);
    expectRows(finalRows);
    expect(initialSignal?.aborted).toBe(true);
    await act(async () => { resolveInitial(statsRead(partialRows)); });
    await flush();
    expectRows(finalRows);
    const readsAfterCompletion = requests.get.mock.calls.length;
    await flush(600000);
    expect(requests.get).toHaveBeenCalledTimes(readsAfterCompletion);
    expect(callsTo(detailUrl())).toHaveLength(2);
  });

  it("reconciles a first completed status while the initial partial stats read is in flight", async () => {
    let statsReads = 0;
    let initialSignal;
    let resolveInitial;
    const initialStats = new Promise((resolve) => { resolveInitial = resolve; });
    requests.get.mockImplementation((url, options) => {
      if (url === detailUrl()) return Promise.resolve(detailRead("Completed"));
      if (url === statsUrl()) {
        statsReads += 1;
        if (statsReads === 1) {
          initialSignal = options?.signal;
          return initialStats;
        }
        return Promise.resolve(statsRead(finalRows));
      }
      throw new Error(`Unexpected GET: ${url}`);
    });
    mountSummary();
    await flush();
    expectRows(finalRows);
    expect(initialSignal?.aborted).toBe(true);
    expect(callsTo(detailUrl())).toHaveLength(1);
    expect(callsTo(statsUrl())).toHaveLength(2);

    await act(async () => { resolveInitial(statsRead(partialRows)); });
    await flush();
    expectRows(finalRows);
    expect(callsTo(statsUrl())).toHaveLength(2);
    const readsAfterCompletion = requests.get.mock.calls.length;
    await flush(600000);
    expect(requests.get).toHaveBeenCalledTimes(readsAfterCompletion);
    expectRows(finalRows);
  });

  it("does not poll an already completed summary", async () => {
    requests.get.mockImplementation(async (url) => {
      if (url === detailUrl()) return detailRead("Completed");
      if (url === statsUrl()) return statsRead(finalRows);
      throw new Error(`Unexpected GET: ${url}`);
    });
    mountSummary();
    await flush();
    expectRows(finalRows);
    expect(callsTo(detailUrl())).toHaveLength(1);
    expect(callsTo(statsUrl())).toHaveLength(2);
    const readsAfterReconciliation = requests.get.mock.calls.length;
    await flush(600000);
    expect(requests.get).toHaveBeenCalledTimes(readsAfterReconciliation);
    expectRows(finalRows);
  });

  it("scopes refreshes to the new experiment after a route change", async () => {
    let otherDetailReads = 0;
    const otherRows = finalRows.map((row) => ({
      ...row,
      experiment_dataset_name: `Second ${row.experiment_dataset_name}`,
    }));
    requests.get.mockImplementation(async (url) => {
      if (url === detailUrl()) return detailRead("Running");
      if (url === statsUrl()) return statsRead(partialRows);
      if (url === detailUrl(otherId)) {
        otherDetailReads += 1;
        return detailRead(otherDetailReads === 1 ? "Running" : "Completed");
      }
      if (url === statsUrl(otherId)) return statsRead(otherDetailReads >= 2 ? otherRows : partialRows);
      throw new Error(`Unexpected GET: ${url}`);
    });
    const client = mountSummary();
    await flush();
    expectRows(partialRows);
    const oldSummary = client.getQueryData(["experiment-summary", experimentId]);
    const oldStatsReads = callsTo(statsUrl()).length;
    const oldDetailReads = callsTo(detailUrl()).length;
    const invalidate = vi.spyOn(client, "invalidateQueries");
    fireEvent.click(screen.getByRole("button", { name: "Open second experiment" }));
    await flush();
    await flush(2000);
    expectRows(otherRows);
    expect(client.getQueryData(["experiment-summary", experimentId])).toBe(oldSummary);
    for (const [filters] of invalidate.mock.calls) {
      expect(filters).toEqual({ queryKey: ["experiment-summary", otherId], exact: true });
    }
    await flush(30000);
    expect(callsTo(statsUrl())).toHaveLength(oldStatsReads);
    expect(callsTo(detailUrl())).toHaveLength(oldDetailReads);
    expect(callsTo(detailUrl(otherId))).toHaveLength(2);
  });

  it.each([404, 503])("stops after a failed status read (%s) and supports explicit recovery", async (status) => {
    let detailReads = 0;
    let recover = false;
    requests.get.mockImplementation(async (url) => {
      if (url === detailUrl()) {
        detailReads += 1;
        if (recover) return detailRead("Completed");
        if (detailReads === 1) return detailRead("Running");
        throw Object.assign(new Error("Status unavailable"), { response: { status } });
      }
      if (url === statsUrl()) return statsRead(recover ? finalRows : partialRows);
      throw new Error(`Unexpected GET: ${url}`);
    });
    mountSummary();
    await flush();
    expectRows(partialRows);
    const initialStatsReads = callsTo(statsUrl()).length;
    await flush(60000);
    expect(callsTo(detailUrl())).toHaveLength(status === 404 ? 2 : 4);
    expect(callsTo(statsUrl())).toHaveLength(initialStatsReads);
    expectRows(partialRows);
    const refresh = screen.getByRole("button", { name: "Refresh status" });
    expect(refresh).toBeEnabled();
    const readsAfterFailure = requests.get.mock.calls.length;
    await flush(600000);
    expect(requests.get).toHaveBeenCalledTimes(readsAfterFailure);

    recover = true;
    fireEvent.click(refresh);
    await flush();
    expectRows(finalRows);
    expect(screen.queryByRole("button", { name: "Refresh status" })).toBeNull();
    const readsAfterRecovery = requests.get.mock.calls.length;
    await flush(600000);
    expect(requests.get).toHaveBeenCalledTimes(readsAfterRecovery);
    expect(requests.post).not.toHaveBeenCalled();
  });
});
