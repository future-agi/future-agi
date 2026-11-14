import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { useEvalLogSourceNavigation } from "../useEvalLogSourceNavigation";

const scope = vi.hoisted(() => ({ org: "org-1", ws: "ws-1" }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: scope.org }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: scope.ws }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { develop: { eval: { getEvalLogs: "/model-hub/get-eval-logs" } } },
}));

const ready = {
  status: "ready",
  kind: "trace",
  project_id: "project-1",
  trace_id: "trace-1",
  span_id: "span-1",
  retryable: false,
};
const response = (nav = ready) => ({
  data: { result: { source_navigation: nav } },
});
let client;
function mount(options = {}) {
  client = new QueryClient();
  return renderHook(() => useEvalLogSourceNavigation("log-1", options), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
}

describe("eval log source enrichment", () => {
  beforeEach(() => {
    scope.org = "org-1";
    scope.ws = "ws-1";
    axios.get.mockReset().mockResolvedValue(response());
  });
  afterEach(() => client?.clear());

  it.each(["org", "ws"])(
    "H01 partitions %s and never returns the previous scope's data",
    async (field) => {
      const { result, rerender } = mount();
      await waitFor(() => expect(result.current.data).toEqual(ready));
      expect(result.current.key).toEqual([
        "evalLogSourceNavigation",
        "org-1",
        "ws-1",
        "log-1",
      ]);
      axios.get.mockImplementation(() => new Promise(() => {}));
      scope[field] = "new-scope";
      rerender();
      expect(result.current.data).toBeUndefined();
      expect(result.current.key).toContain("new-scope");
    },
  );

  it("H02 handles an older backend without an unpinned fallback", async () => {
    axios.get.mockResolvedValue({
      data: { result: { trace_id: "copy-only" } },
    });
    const { result } = mount();
    await waitFor(() =>
      expect(result.current.data).toEqual({
        status: "unsupported_backend",
        retryable: false,
        kind: null,
        project_id: null,
        trace_id: null,
        span_id: null,
      }),
    );
    expect(axios.get).toHaveBeenCalledOnce();
  });

  it("H03 sends the declared opt-in wire value and uses explicit-only freshness settings", async () => {
    const { result } = mount();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledWith("/model-hub/get-eval-logs", {
      params: { log_id: "log-1", include_source_navigation: "true" },
      signal: expect.any(AbortSignal),
    });
    expect(
      client.getQueryCache().find({ queryKey: result.current.key }).options,
    ).toMatchObject({
      staleTime: 0,
      gcTime: 0,
      retry: false,
      refetchOnWindowFocus: false,
      meta: { errorHandled: true },
    });
  });

  it("H04 retries a failed enrichment only on explicit revalidation", async () => {
    axios.get.mockRejectedValueOnce({ statusCode: 500 });
    const { result } = mount();
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(axios.get).toHaveBeenCalledOnce();
    let fresh;
    await act(async () => {
      fresh = await result.current.revalidate();
    });
    expect(fresh).toEqual(ready);
    expect(axios.get).toHaveBeenCalledTimes(2);
  });

  it("H05 deduplicates concurrent revalidation against the in-flight read", async () => {
    let resolve;
    axios.get.mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const { result } = mount();
    const first = result.current.revalidate();
    const second = result.current.revalidate();
    expect(axios.get).toHaveBeenCalledOnce();
    await act(async () => {
      resolve(response());
      expect(await first).toEqual(ready);
      expect(await second).toEqual(ready);
    });
  });

  it("H06 aborts on unmount and ignores a late success", async () => {
    let resolve;
    axios.get.mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const { result, unmount } = mount();
    const key = result.current.key;
    const signal = axios.get.mock.calls[0][1].signal;
    unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => {
      resolve(response());
    });
    await waitFor(() => expect(client.getQueryData(key)).toBeUndefined());
  });

  it("H07 does not fetch for an unopened log", () => {
    mount({ enabled: false });
    expect(axios.get).not.toHaveBeenCalled();
  });
});
