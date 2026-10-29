import React from "react";
import { act, renderHook } from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

const axiosMod = await import("src/utils/axios");
const axios = axiosMod.default;
const { endpoints } = axiosMod;
const { enqueueSnackbar } = await import("notistack");
const { handleError } = await import("src/utils/queryErrorHandler");
const { listMatchingCalls, uniqueScenarioKeys, useRerunCallsInRun } =
  await import("../rerunScenarios");

describe("uniqueScenarioKeys", () => {
  it("reduces calls to their scenarios, once each, skipping calls without one", () => {
    expect(
      uniqueScenarioKeys([
        { sourceScenarioKey: "refund" },
        { sourceScenarioKey: "refund" },
        { sourceScenarioKey: "escalate" },
        { sourceScenarioKey: null },
      ]),
    ).toEqual(["refund", "escalate"]);
  });
});

describe("listMatchingCalls", () => {
  beforeEach(() => axios.get.mockReset());

  it("walks every page of the filtered calls, flat, and skips the unticked ones", async () => {
    axios.get
      .mockResolvedValueOnce({
        data: {
          total_pages: 2,
          results: [
            { id: "c1", source_scenario_key: "refund" },
            { id: "c2", source_scenario_key: "refund" },
            { id: "c3", source_scenario_key: "escalate" },
          ],
        },
      })
      .mockResolvedValueOnce({
        data: {
          total_pages: 2,
          results: [
            { id: "c4", source_scenario_key: "timeout" },
            { id: "c5", source_scenario_key: null },
          ],
        },
      });

    const calls = await listMatchingCalls("ex1", { status: ["failed"] }, [
      "c3",
    ]);

    expect(calls).toEqual([
      { id: "c1", sourceScenarioKey: "refund" },
      { id: "c2", sourceScenarioKey: "refund" },
      { id: "c4", sourceScenarioKey: "timeout" },
    ]);
    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(axios.get).toHaveBeenNthCalledWith(
      1,
      endpoints.runResultsV3.calls("ex1"),
      {
        params: {
          page: 1,
          page_size: 500,
          search: "",
          filters: JSON.stringify({ status: ["failed"] }),
        },
      },
    );
    expect(axios.get.mock.calls[1][1].params.page).toBe(2);
  });
});

describe("useRerunCallsInRun", () => {
  beforeEach(() => vi.mocked(enqueueSnackbar).mockReset());

  // The app's own client: refusals without a `result` get no global toast.
  const renderRerun = () => {
    const client = new QueryClient({
      mutationCache: new MutationCache({ onError: handleError }),
      defaultOptions: { mutations: { retry: false } },
    });
    const wrapper = ({ children }) =>
      React.createElement(QueryClientProvider, { client }, children);
    return renderHook(() => useRerunCallsInRun("ex1"), { wrapper });
  };

  it("tells the user once why the run refused the re-run", async () => {
    axios.post.mockRejectedValueOnce({
      error: "rerun_in_progress",
      message:
        "this run is still running or grading; re-run its calls once it finishes",
      retryable: false,
      statusCode: 409,
    });
    const { result } = renderRerun();

    await act(async () => {
      await result.current.mutateAsync(["c1"]).catch(() => {});
    });

    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "this run is still running or grading; re-run its calls once it finishes",
      { variant: "error" },
    );
  });
});
