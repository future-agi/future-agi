import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  listHarnessEnvironments: vi.fn(),
  deleteHarnessEnvironment: vi.fn(),
  renameHarnessEnvironment: vi.fn(),
  deleteAppliedEvaluation: vi.fn(),
}));

const {
  renameHarnessEnvironment,
  deleteAppliedEvaluation,
} = await import("src/api/simulate-environments/harnessEnvironments");
const { useRenameEnvironment, useRemoveAppliedEvaluation } = await import(
  "src/api/simulate-environments/environments"
);
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
