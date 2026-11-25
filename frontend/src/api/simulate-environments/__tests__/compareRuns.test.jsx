import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
}));

const { default: axios } = await import("src/utils/axios");
const { useCompareRuns, compareRunsKey } = await import("../compareRuns");
const { compareRunsKeyPrefix } = await import("../runEvals");

let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
wrapper.propTypes = { children: PropTypes.node };

const PATH = "/simulate/api/harness-environments/env-1/compare/";
const RESPONSE = {
  execution_id: "run-2",
  against_execution_id: "run-1",
  pairs: [],
  summary: { paired: 0 },
  versions: { current: {}, against: null },
};

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retryDelay: 0 } },
  });
  axios.get.mockReset();
});

describe("useCompareRuns", () => {
  it("asks for the previous run when none is picked", async () => {
    axios.get.mockResolvedValue({ data: RESPONSE });

    const { result } = renderHook(
      () => useCompareRuns("env-1", "run-2", null),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledWith(PATH, {
      params: { execution_id: "run-2" },
    });
    expect(result.current.data).toEqual(RESPONSE);
    expect(client.getQueryData(compareRunsKey("env-1", "run-2"))).toEqual(
      RESPONSE,
    );
    // Refreshed with the run's analytics.
    expect(compareRunsKey("env-1", "run-2").slice(0, 1)).toEqual(
      compareRunsKeyPrefix,
    );
  });

  it("sends the picked run", async () => {
    axios.get.mockResolvedValue({ data: RESPONSE });

    const { result } = renderHook(
      () => useCompareRuns("env-1", "run-2", "run-0"),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledWith(PATH, {
      params: { execution_id: "run-2", against_execution_id: "run-0" },
    });
  });

  it("waits for the environment and the run", () => {
    renderHook(() => useCompareRuns(undefined, "run-2", null), { wrapper });
    renderHook(() => useCompareRuns("env-1", undefined, null), { wrapper });

    expect(axios.get).not.toHaveBeenCalled();
  });

  it("does not ask again after a refusal", async () => {
    axios.get.mockRejectedValue({ statusCode: 404 });

    const { result } = renderHook(
      () => useCompareRuns("env-1", "run-2", null),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(axios.get).toHaveBeenCalledTimes(1);
  });

  it("asks again after a server failure", async () => {
    axios.get
      .mockRejectedValueOnce({ statusCode: 503 })
      .mockResolvedValue({ data: RESPONSE });

    const { result } = renderHook(
      () => useCompareRuns("env-1", "run-2", null),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledTimes(2);
  });
});
