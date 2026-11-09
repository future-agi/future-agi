import React from "react";
import PropTypes from "prop-types";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
  onlineManager,
} from "@tanstack/react-query";
import { useRunConfigs } from "src/api/develop/r2";
import axios from "src/utils/axios";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    develop: {
      datasetRuns: { list: (id) => `/model-hub/datasets/${id}/runs/` },
    },
  },
}));
vi.mock("src/api/develop/runs", () => ({
  datasetRunsKey: (id) => ["develop", "dataset-runs", id],
  datasetRunsParams: (params) => params,
}));
vi.mock("src/api/develop/run-results", () => ({ runResultsKey: () => [] }));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: vi.fn(),
}));
vi.mock("src/api/project/saved-views", () => ({
  useCreateWorkspaceSavedView: vi.fn(),
  useDeleteWorkspaceSavedView: vi.fn(),
  useGetWorkspaceSavedViews: vi.fn(),
}));

let client;
let current;
function Reader({ datasetId = "owned-dataset" }) {
  current = useRunConfigs(datasetId);
  return <span>{current.isError ? "Unavailable" : "Loading"}</span>;
}
Reader.propTypes = { datasetId: PropTypes.string };
function Host({ visible = true, datasetId }) {
  return (
    <QueryClientProvider client={client}>
      {visible && <Reader datasetId={datasetId} />}
    </QueryClientProvider>
  );
}
Host.propTypes = { visible: PropTypes.bool, datasetId: PropTypes.string };
const tick = async (ms = 25) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};

beforeEach(() => {
  vi.useFakeTimers();
  axios.get.mockReset();
  client = new QueryClient({
    defaultOptions: { queries: { retry: 1, retryDelay: 10, gcTime: 60_000 } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  focusManager.setFocused(undefined);
  onlineManager.setOnline(true);
  vi.useRealTimers();
});

describe("unavailable experiment run configurations", () => {
  it("makes one request for a 404, including repeated observer remounts and focus/reconnect", async () => {
    axios.get.mockRejectedValue({ statusCode: 404, message: "Not found" });
    const view = render(<Host />);
    await tick(5_000);
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    for (let i = 0; i < 20; i += 1) {
      view.rerender(<Host visible={false} />);
      await tick();
      view.rerender(<Host />);
      await tick();
      await act(async () => {
        focusManager.setFocused(false);
        focusManager.setFocused(true);
        onlineManager.setOnline(false);
        onlineManager.setOnline(true);
      });
      await tick(2_000);
    }
    expect(axios.get).toHaveBeenCalledTimes(1);
    expect(axios.get.mock.calls[0][0]).toBe(
      "/model-hub/datasets/owned-dataset/runs/",
    );
  });

  it("allows a deliberate retry to recover and still loads a different dataset", async () => {
    axios.get.mockRejectedValue({ statusCode: 404 });
    const view = render(<Host />);
    await tick(5_000);
    expect(axios.get).toHaveBeenCalledTimes(1);
    axios.get.mockResolvedValue({
      data: { result: { results: [{ run_id: "run-a" }] } },
    });
    await act(async () => {
      await current.refetch();
    });
    await tick();
    expect(current.data).toEqual({ results: [{ run_id: "run-a" }] });
    expect(axios.get).toHaveBeenCalledTimes(2);
    view.rerender(<Host datasetId="second-dataset" />);
    await tick();
    expect(axios.get).toHaveBeenCalledTimes(3);
    expect(axios.get.mock.calls[2][0]).toBe(
      "/model-hub/datasets/second-dataset/runs/",
    );
  });

  it("backs off transient failures and stops after two retries", async () => {
    const calls = [];
    axios.get.mockImplementation(async () => {
      calls.push(Date.now());
      throw { statusCode: 503, message: "Temporarily unavailable" };
    });
    render(<Host />);
    await tick(45_000);
    expect(calls).toHaveLength(3);
    expect(calls[1] - calls[0]).toBeGreaterThanOrEqual(1_000);
    expect(calls[2] - calls[1]).toBeGreaterThanOrEqual(2_000);
    expect(current.isError).toBe(true);
  });
});
