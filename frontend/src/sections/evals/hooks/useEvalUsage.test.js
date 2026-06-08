import { describe, expect, it, vi, beforeEach } from "vitest";
import { useEvalUsageLogs } from "./useEvalUsage";

const mocks = vi.hoisted(() => ({
  useQuery: vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: mocks.useQuery,
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
  },
  endpoints: {
    develop: {
      eval: {
        getEvalUsage: (templateId) => `/api/evals/${templateId}/usage`,
      },
    },
  },
}));

describe("useEvalUsageLogs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useQuery.mockReturnValue({ data: null });
  });

  it("keeps log polling disabled by default", () => {
    useEvalUsageLogs("eval-1");

    expect(mocks.useQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        enabled: true,
        keepPreviousData: true,
        queryKey: ["evals", "usage-logs", "eval-1", "30d", 0, 25],
        refetchInterval: false,
      }),
    );
  });

  it("forwards a scoped refetch interval for onboarding review runs", () => {
    useEvalUsageLogs("eval-1", {
      page: 1,
      pageSize: 10,
      period: "7d",
      refetchInterval: 2000,
    });

    expect(mocks.useQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ["evals", "usage-logs", "eval-1", "7d", 1, 10],
        refetchInterval: 2000,
      }),
    );
  });
});
