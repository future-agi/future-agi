import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  listHarnessEnvironments: vi.fn(),
  deleteHarnessEnvironment: vi.fn(),
  renameHarnessEnvironment: vi.fn(),
  deleteAppliedEvaluation: vi.fn(),
  updateAppliedEvaluation: vi.fn(),
}));

const {
  renameHarnessEnvironment,
  deleteAppliedEvaluation,
  updateAppliedEvaluation,
} = await import("src/api/simulate-environments/harnessEnvironments");
const {
  useRenameEnvironment,
  useRemoveAppliedEvaluation,
  useEditAppliedEvaluation,
  environmentRunTestKey,
} = await import("src/api/simulate-environments/environments");
const { harnessEnvironmentKey } = await import(
  "src/api/simulate-environments/environment"
);

const makeWrapper = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
};

describe("useRenameEnvironment (§8)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("seeds the detail cache from the returned §6 body and invalidates the list", async () => {
    const detailBody = { id: "env-1", overview: { name: "Renamed" } };
    renameHarnessEnvironment.mockResolvedValue(detailBody);
    const { client, wrapper } = makeWrapper();
    const setSpy = vi.spyOn(client, "setQueryData");
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");

    const { result } = renderHook(() => useRenameEnvironment(), { wrapper });
    result.current.mutate({ id: "env-1", name: "Renamed" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(renameHarnessEnvironment).toHaveBeenCalledWith("env-1", "Renamed");
    expect(setSpy).toHaveBeenCalledWith(harnessEnvironmentKey("env-1"), detailBody);
    expect(invalidateSpy).toHaveBeenCalled();
  });
});

describe("useRemoveAppliedEvaluation (§9)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("invalidates the detail query so evaluations.selected is re-read", async () => {
    deleteAppliedEvaluation.mockResolvedValue(undefined);
    const { client, wrapper } = makeWrapper();
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");

    const { result } = renderHook(() => useRemoveAppliedEvaluation(), { wrapper });
    result.current.mutate({ id: "env-1", evalConfigId: "eval-1" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(deleteAppliedEvaluation).toHaveBeenCalledWith("env-1", "eval-1");
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: harnessEnvironmentKey("env-1"),
    });
  });
});

describe("useEditAppliedEvaluation", () => {
  beforeEach(() => vi.clearAllMocks());

  const RAW = {
    id: "rt-1",
    simulate_eval_configs_detail: [
      { id: "eval-1", name: "a", mapping: { conversation: "transcript" } },
      { id: "eval-2", name: "b", mapping: {} },
    ],
  };
  const UPDATED = {
    id: "eval-1",
    name: "a",
    mapping: { conversation: "voice_recording" },
  };
  const vars = {
    id: "env-1",
    evalConfigId: "eval-1",
    runTestId: "rt-1",
    body: { mapping: { conversation: "voice_recording" } },
  };

  it("sends the edit and resolves the updated eval", async () => {
    updateAppliedEvaluation.mockResolvedValue(UPDATED);
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useEditAppliedEvaluation(), {
      wrapper,
    });

    await expect(result.current.mutateAsync(vars)).resolves.toEqual(UPDATED);
    expect(updateAppliedEvaluation).toHaveBeenCalledWith(
      "env-1",
      "eval-1",
      vars.body,
    );
  });

  it("replaces only the edited row in the cached run test", async () => {
    updateAppliedEvaluation.mockResolvedValue(UPDATED);
    const { client, wrapper } = makeWrapper();
    client.setQueryData(environmentRunTestKey("rt-1"), RAW);
    const { result } = renderHook(() => useEditAppliedEvaluation(), {
      wrapper,
    });

    await result.current.mutateAsync(vars);
    const cached = client.getQueryData(environmentRunTestKey("rt-1"));
    expect(cached.id).toBe("rt-1");
    expect(cached.simulate_eval_configs_detail).toEqual([
      UPDATED,
      RAW.simulate_eval_configs_detail[1],
    ]);
  });

  it("writes nothing when the run test isn't cached", async () => {
    updateAppliedEvaluation.mockResolvedValue(UPDATED);
    const { client, wrapper } = makeWrapper();
    const { result } = renderHook(() => useEditAppliedEvaluation(), {
      wrapper,
    });

    await result.current.mutateAsync(vars);
    expect(client.getQueryData(environmentRunTestKey("rt-1"))).toBeUndefined();
  });

  it("refreshes the environment detail", async () => {
    updateAppliedEvaluation.mockResolvedValue(UPDATED);
    const { client, wrapper } = makeWrapper();
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useEditAppliedEvaluation(), {
      wrapper,
    });

    await result.current.mutateAsync(vars);
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: harnessEnvironmentKey("env-1"),
    });
  });

  it("marks its errors as handled by the caller", () => {
    const { client, wrapper } = makeWrapper();
    renderHook(() => useEditAppliedEvaluation(), { wrapper });
    // The drawer shows the refusal itself; the global toast must stay quiet.
    const { result } = renderHook(() => useEditAppliedEvaluation(), {
      wrapper,
    });
    result.current.mutate(vars);
    expect(
      client.getMutationCache().getAll().at(-1).options.meta,
    ).toEqual({ errorHandled: true });
  });

  it("re-reads both lists when the edit fails, so a row gone elsewhere drops out", async () => {
    updateAppliedEvaluation.mockRejectedValue({
      statusCode: 404,
      detail: "Evaluation not found",
    });
    const { client, wrapper } = makeWrapper();
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useEditAppliedEvaluation(), {
      wrapper,
    });

    await expect(result.current.mutateAsync(vars)).rejects.toBeTruthy();
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: environmentRunTestKey("rt-1"),
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: harnessEnvironmentKey("env-1"),
    });
  });

  it("leaves the cached list alone when the edit is refused", async () => {
    updateAppliedEvaluation.mockRejectedValue({
      statusCode: 400,
      detail: "Nothing to change",
    });
    const { client, wrapper } = makeWrapper();
    client.setQueryData(environmentRunTestKey("rt-1"), RAW);
    const { result } = renderHook(() => useEditAppliedEvaluation(), {
      wrapper,
    });

    await expect(result.current.mutateAsync(vars)).rejects.toBeTruthy();
    expect(client.getQueryData(environmentRunTestKey("rt-1"))).toBe(RAW);
  });
});
