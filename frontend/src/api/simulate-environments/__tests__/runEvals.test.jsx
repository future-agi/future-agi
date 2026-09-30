import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock only the axios default instance; keep the real `endpoints` so the URL
// assertions below are genuine, not a tautology against our own mock.
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post: vi.fn() },
}));

const axiosMod = await import("src/utils/axios");
const axios = axiosMod.default;
const { endpoints } = axiosMod;
const { useRunNewEvals } = await import("../runEvals");

const makeWrapper = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
};

describe("useRunNewEvals", () => {
  beforeEach(() => {
    axios.post.mockReset();
  });

  it("posts this run and the chosen evals to run-new-evals", async () => {
    const body = { message: "ok", run_test_id: "rt1", call_execution_count: 4 };
    axios.post.mockResolvedValue({ data: body });
    const { wrapper } = makeWrapper();

    const { result } = renderHook(() => useRunNewEvals(), { wrapper });
    result.current.mutate({
      runTestId: "rt1",
      executionId: "ex1",
      evalConfigIds: ["c1", "c2"],
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.post).toHaveBeenCalledWith(
      endpoints.runTests.runEvals("rt1"),
      {
        test_execution_ids: ["ex1"],
        eval_config_ids: ["c1", "c2"],
      },
    );
    expect(result.current.data).toEqual(body);
  });

  it("refreshes the run's header, table, analytics and call drawers once dispatched", async () => {
    axios.post.mockResolvedValue({ data: {} });
    const { client, wrapper } = makeWrapper();
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");

    const { result } = renderHook(() => useRunNewEvals(), { wrapper });
    result.current.mutate({
      runTestId: "rt1",
      executionId: "ex1",
      evalConfigIds: ["c1", "c2"],
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["simulation-run-results-v3", "ex1"],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["simulation-run-analytics-v3", "ex1"],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["simulation-call-detail-v3"],
    });
  });

  it("refreshes nothing when the request is refused", async () => {
    axios.post.mockRejectedValue({ statusCode: 400, message: "no" });
    const { client, wrapper } = makeWrapper();
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");

    const { result } = renderHook(() => useRunNewEvals(), { wrapper });
    result.current.mutate({
      runTestId: "rt1",
      executionId: "ex1",
      evalConfigIds: ["c1", "c2"],
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(invalidateSpy).not.toHaveBeenCalled();
  });
});
