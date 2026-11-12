import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";

import { useOldestRequestTime } from "../hooks/useRequestLogs";

const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => api.get(...args) },
  endpoints: {
    gateway: {
      requestLogs: "/agentcc/request-logs/",
      requestLogSearch: "/agentcc/request-logs/search/",
    },
  },
}));

const wrapper = ({ children }) => (
  <QueryClientProvider client={new QueryClient()}>
    {children}
  </QueryClientProvider>
);

beforeEach(() => {
  api.get.mockReset();
  api.get.mockResolvedValue({
    data: { count: 617, results: [{ started_at: "2026-10-20T09:38:06Z" }] },
  });
});

// The Logs volume strip's window, when the table has no start time: the
// oldest request that matches the table's filters, one row, oldest first.
describe("useOldestRequestTime", () => {
  it("asks the list for one row, oldest first, with the table's filters", async () => {
    const { result } = renderHook(
      () =>
        useOldestRequestTime({
          model: "gpt-4o",
          apiKeyId: "key-1",
          page: "3",
          sort: "-cost",
          log: "log-1",
        }),
      { wrapper },
    );
    await waitFor(() =>
      expect(result.current.data).toBe("2026-10-20T09:38:06Z"),
    );
    const [url, { params }] = api.get.mock.calls[0];
    expect(url).toBe("/agentcc/request-logs/");
    expect(params).toEqual({
      model: "gpt-4o",
      api_key_id: "key-1",
      page: 1,
      limit: 1,
      ordering: "started_at",
    });
    expect(
      validateContractedRequestConfig({ url, method: "get", params }),
    ).toMatchObject({ ok: true });
  });

  it("with a search, asks the search endpoint the same way, as the table does", async () => {
    const { result } = renderHook(
      () => useOldestRequestTime({ search: "timeout", isError: "true" }),
      { wrapper },
    );
    await waitFor(() =>
      expect(result.current.data).toBe("2026-10-20T09:38:06Z"),
    );
    const [url, { params }] = api.get.mock.calls[0];
    expect(url).toBe("/agentcc/request-logs/search/");
    expect(params).toEqual({
      q: "timeout",
      is_error: "true",
      page: 1,
      limit: 1,
      ordering: "started_at",
    });
    expect(
      validateContractedRequestConfig({ url, method: "get", params }),
    ).toMatchObject({ ok: true });
  });

  it("is null when no request matches", async () => {
    api.get.mockResolvedValue({ data: { count: 0, results: [] } });
    const { result } = renderHook(() => useOldestRequestTime({}), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it("asks nothing while disabled", () => {
    renderHook(() => useOldestRequestTime({}, { enabled: false }), { wrapper });
    expect(api.get).not.toHaveBeenCalled();
  });
});
