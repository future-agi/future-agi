import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock only the axios default instance; the real `apiPath` builds the URL, so
// the path asserted below is the one the generated contract allows.
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post: vi.fn() },
}));

const axios = (await import("src/utils/axios")).default;
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

  it("posts the chosen evals to this run's re-grade route", async () => {
    const body = { call_execution_count: 4 };
    axios.post.mockResolvedValue({ data: body });
    const { wrapper } = makeWrapper();

    const { result } = renderHook(() => useRunNewEvals(), { wrapper });
    result.current.mutate({
      id: "env-1",
      executionId: "ex1",
      evalConfigIds: ["c1", "c2"],
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.post).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/runs/ex1/evaluations/run/",
      { eval_config_ids: ["c1", "c2"] },
    );
    expect(result.current.data).toEqual(body);
  });

  it("refreshes the run's header, table, analytics and call drawers once dispatched", async () => {
    axios.post.mockResolvedValue({ data: {} });
    const { client, wrapper } = makeWrapper();
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");

    const { result } = renderHook(() => useRunNewEvals(), { wrapper });
    result.current.mutate({
      id: "env-1",
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

  it.each([400, 403])(
    "refreshes nothing when the request is refused with a %s",
    async (statusCode) => {
      axios.post.mockRejectedValue({ statusCode, message: "no" });
      const { client, wrapper } = makeWrapper();
      const invalidateSpy = vi.spyOn(client, "invalidateQueries");

      const { result } = renderHook(() => useRunNewEvals(), { wrapper });
      result.current.mutate({
        id: "env-1",
        executionId: "ex1",
        evalConfigIds: ["c1", "c2"],
      });

      await waitFor(() => expect(result.current.isError).toBe(true));
      expect(axios.post).toHaveBeenCalledTimes(1);
      expect(invalidateSpy).not.toHaveBeenCalled();
    },
  );

  // The interceptor leaves statusCode unset when no response came back.
  it.each([
    ["a gateway timeout", { statusCode: 504, message: "Gateway Timeout" }],
    ["a server error", { statusCode: 500, message: "Server Error" }],
    [
      "a dropped connection",
      { statusCode: undefined, message: "Network Error" },
    ],
    [
      "a conflict",
      { statusCode: 409, message: "Grading is already running on this run." },
    ],
  ])(
    "refreshes the run's header after %s, since grading may be under way",
    async (_label, error) => {
      axios.post.mockRejectedValue(error);
      const { client, wrapper } = makeWrapper();
      const invalidateSpy = vi.spyOn(client, "invalidateQueries");

      const { result } = renderHook(() => useRunNewEvals(), { wrapper });
      result.current.mutate({
        id: "env-1",
        executionId: "ex1",
        evalConfigIds: ["c1", "c2"],
      });

      await waitFor(() => expect(result.current.isError).toBe(true));
      expect(axios.post).toHaveBeenCalledTimes(1);
      expect(invalidateSpy).toHaveBeenCalledTimes(1);
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["simulation-run-results-v3", "ex1"],
      });
    },
  );
});
