import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";

import { useBudgetSpend } from "./useBudgetSpend";

// The hook the Budgets page's bars read, run for real against a mocked
// axios: one GET /agentcc/spend-summary/ per period, the session's one
// organization picked from the answer.

const mockGet = vi.fn();
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => mockGet(...args) },
}));

function wrapper({ children }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const answer = (total) => ({
  data: {
    status: true,
    result: {
      orgs: { "org-1": { total_spend: total, per_key: {}, per_user: {} } },
    },
  },
});

describe("useBudgetSpend", () => {
  // A block body: an arrow returning the mock would be run as a cleanup hook.
  beforeEach(() => {
    mockGet.mockReset();
  });

  it("reads each period's organization spend from spend-summary", async () => {
    mockGet.mockImplementation((url, { params }) =>
      Promise.resolve(answer(params.period === "daily" ? 3 : 40)),
    );
    const { result } = renderHook(() => useBudgetSpend(["daily", "monthly"]), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data.daily.total_spend).toBe(3);
    expect(result.current.data.monthly.total_spend).toBe(40);
    expect(mockGet).toHaveBeenCalledTimes(2);
    expect(mockGet.mock.calls[0][0]).toMatch(/\/agentcc\/spend-summary\/$/);
    expect(mockGet.mock.calls.map((c) => c[1].params.period).sort()).toEqual([
      "daily",
      "monthly",
    ]);
  });

  it("an organization with no spend yet reads $0", async () => {
    mockGet.mockResolvedValue({ data: { result: { orgs: {} } } });
    const { result } = renderHook(() => useBudgetSpend(["monthly"]), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data.monthly.total_spend).toBe(0);
  });

  it("asks for nothing when no org-level budget is set", () => {
    const { result } = renderHook(() => useBudgetSpend([]), { wrapper });
    expect(result.current.data).toEqual({});
    expect(mockGet).not.toHaveBeenCalled();
  });

  it("reports a failed read", async () => {
    mockGet.mockRejectedValue(new Error("403"));
    const { result } = renderHook(() => useBudgetSpend(["monthly"]), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data.monthly).toBeUndefined();
  });
});
