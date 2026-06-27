import { describe, expect, it, vi } from "vitest";
import { invalidateEvalDeletionQueries } from "./eval_cache";

describe("invalidateEvalDeletionQueries unit", () => {
  it("invalidates eval detail, KPI, and analytics queries for selected executions", () => {
    const queryClient = {
      invalidateQueries: vi.fn(),
    };

    invalidateEvalDeletionQueries(queryClient, "test-1", [
      "execution-1",
      "execution-2",
    ]);

    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["test-runs-detail", "test-1"],
    });
    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["test-execution-detail", "KPIS", "execution-1"],
    });
    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["test-execution-detail", "KPIS", "execution-2"],
    });
    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["test-execution-analytics", "test-1"],
    });
    expect(queryClient.invalidateQueries).toHaveBeenCalledTimes(4);
  });

  it("invalidates the KPI subtree when no execution ids are provided", () => {
    const queryClient = {
      invalidateQueries: vi.fn(),
    };

    invalidateEvalDeletionQueries(queryClient, "test-1", null);

    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["test-runs-detail", "test-1"],
    });
    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["test-execution-detail", "KPIS"],
    });
    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["test-execution-analytics", "test-1"],
    });
    expect(queryClient.invalidateQueries).toHaveBeenCalledTimes(3);
  });
});
