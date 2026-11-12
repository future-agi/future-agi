import React from "react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRequestCount } from "./useRequestCount";

const state = vi.hoisted(() => ({ get: vi.fn(), organizationId: "org-a" }));
vi.mock("src/utils/axios", () => ({
  default: { get: state.get },
  endpoints: { gateway: { requestLogs: "/request-logs" } },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: state.organizationId }),
}));

let client;
function wrapper({ children }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
wrapper.propTypes = { children: PropTypes.node };
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  state.organizationId = "org-a";
  state.get.mockReset();
});
afterEach(() => client.clear());

describe("useRequestCount", () => {
  it("counts lifetime 2xx, non-error requests and completes only after a manual recheck", async () => {
    let successful = 0;
    state.get.mockImplementation(async (_url, { params }) => ({
      data: { count: params.min_status_code === 200 && params.max_status_code === 299 && params.is_error === false ? successful : 4 },
    }));
    const { result } = renderHook(() => useRequestCount(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBe(0);
    expect(state.get.mock.calls[0][1].params).toEqual({
      limit: 1, min_status_code: 200, max_status_code: 299, is_error: false,
    });
    successful = 1;
    await act(async () => { await result.current.refetch(); });
    expect(result.current.data).toBe(1);
    expect(state.get).toHaveBeenCalledTimes(2);
  });

  it.each([{ count: 0 }, { count: "12" }, { result: { count: 7 } }])(
    "accepts the represented list/count envelopes: %j", async (data) => {
      state.get.mockResolvedValue({ data });
      const { result } = renderHook(() => useRequestCount(), { wrapper });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(result.current.data).toBe(Number(data.count ?? data.result.count));
    },
  );

  it.each([undefined, null, "", " ", false, [], {}, -1, 1.5, "invalid", Infinity, Number.MAX_SAFE_INTEGER + 1])(
    "rejects malformed counts instead of using a row or zero: %j", async (count) => {
      state.get.mockResolvedValue({ data: { count, results: [{ status_code: 200 }] } });
      const { result } = renderHook(() => useRequestCount(), { wrapper });
      await waitFor(() => expect(result.current.isError).toBe(true));
      expect(result.current.data).toBeUndefined();
      expect(result.current.error.message).toMatch(/could not check/i);
    },
  );

  it("exposes a failed read and allows retry without permanent polling", async () => {
    state.get.mockRejectedValueOnce(new Error("Read failed"));
    const { result } = renderHook(() => useRequestCount(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
    const query = client.getQueryCache().getAll()[0];
    expect(query.options.refetchInterval).toBeFalsy();
    expect(query.options.refetchOnWindowFocus).toBe(false);
    state.get.mockResolvedValue({ data: { count: 1 } });
    await act(async () => { await result.current.refetch(); });
    expect(result.current.isSuccess).toBe(true);
    expect(result.current.data).toBe(1);
  });

  it("does not reuse another organization's successful count", async () => {
    state.get.mockResolvedValueOnce({ data: { count: 1 } }).mockResolvedValueOnce({ data: { count: 0 } });
    const { result, rerender } = renderHook(() => useRequestCount(), { wrapper });
    await waitFor(() => expect(result.current.data).toBe(1));
    state.organizationId = "org-b";
    rerender();
    expect(result.current.data).toBeUndefined();
    await waitFor(() => expect(result.current.data).toBe(0));
    expect(state.get).toHaveBeenCalledTimes(2);
  });

  it("keeps the existing enabled gate", () => {
    renderHook(() => useRequestCount({ enabled: false }), { wrapper });
    expect(state.get).not.toHaveBeenCalled();
  });
});
