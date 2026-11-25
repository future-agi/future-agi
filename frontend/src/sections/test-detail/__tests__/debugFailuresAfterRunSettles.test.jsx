import { act, renderHook, waitFor } from "@testing-library/react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock only the axios default instance; the real useKpis, its query options
// and the real QueryClient do the rest.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn() } };
});

const { default: axios } = await import("src/utils/axios");
const { kpisQueryOptions } = await import("src/hooks/useKpis");
const { invalidateSettledRun, useFixMyAgentBlocked } = await import(
  "../common"
);
const { useTestExecutionStore } = await import("../states");

const EXECUTION_ID = "e1";
const BLOCKED_REASON =
  "You need at least 15 connected calls to debug failures.";
const kpis = (connected_calls) => ({
  agent_type: "voice",
  total_calls: 20,
  connected_calls,
});

// The run page polls KPIs while the run is in progress. Its last poll is what
// the cache holds when the grid sees the run finish.
const renderOnRunWithLastPoll = (connectedCalls) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(
    kpisQueryOptions(EXECUTION_ID).queryKey,
    kpis(connectedCalls),
  );
  useTestExecutionStore.setState({ status: "running" });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  const { result } = renderHook(() => useFixMyAgentBlocked(EXECUTION_ID), {
    wrapper: Wrapper,
  });
  return { queryClient, result };
};

// What the grid does when its refresh reads a finished status.
const runSettles = (queryClient) => {
  act(() => {
    useTestExecutionStore.setState({ status: "completed" });
    invalidateSettledRun(queryClient, EXECUTION_ID);
  });
};

beforeEach(() => {
  axios.get.mockReset();
});

describe("Debug failures once the run finishes", () => {
  it("opens when the final count reaches 15 after a last poll of 14", async () => {
    const { queryClient, result } = renderOnRunWithLastPoll(14);
    expect(result.current.disabled).toBe(true);
    axios.get.mockResolvedValue({ data: kpis(15), status: 200 });

    runSettles(queryClient);

    await waitFor(() =>
      expect(result.current).toEqual({ disabled: false, reason: "" }),
    );
    expect(axios.get).toHaveBeenCalledTimes(1);
  });

  it("stays open when the last poll already read 15", async () => {
    const { queryClient, result } = renderOnRunWithLastPoll(15);
    axios.get.mockResolvedValue({ data: kpis(15), status: 200 });

    runSettles(queryClient);

    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(result.current).toEqual({ disabled: false, reason: "" }),
    );
  });

  it("stays blocked when the final count is still 14", async () => {
    const { queryClient, result } = renderOnRunWithLastPoll(14);
    axios.get.mockResolvedValue({ data: kpis(14), status: 200 });

    runSettles(queryClient);

    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(result.current).toEqual({
        disabled: true,
        reason: BLOCKED_REASON,
      }),
    );
  });
});
