import React from "react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { endpoints } from "src/utils/axios";

import { useRouteHealth } from "./useRouteHealth";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, ...api } };
});

const ROUTES = [
  { key: "managed", label: "Future AGI", managed: true, sendAs: "openai/gpt-4o" },
  { key: "own:openai", label: "OpenAI", provider: "openai", sendAs: "gpt-4o" },
];
const MODEL = "openai/gpt-4o";
const FIRST = "2026-11-09T10:00:12.345Z";
const NEXT = "2026-11-09T10:05:37.456Z";

function response(params, count = 5) {
  return {
    data: {
      count: params.is_error ? 1 : count,
      results: params.ordering ? [{ latency_ms: params.page === 3 ? 120 : 400 }] : [],
    },
  };
}

let client;
function Wrapper({ children }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
Wrapper.propTypes = { children: PropTypes.node };

const open = (routes = ROUTES, modelId = MODEL) =>
  renderHook((props) => useRouteHealth(props.routes, props.modelId), {
    wrapper: Wrapper,
    initialProps: { routes, modelId },
  });
const settled = (result) =>
  waitFor(() => expect(result.current.items.every((item) => !item.isFetching)).toBe(true));

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(FIRST));
  api.get.mockReset();
  api.post.mockReset();
  api.get.mockImplementation(async (_url, { params }) => response(params));
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(() => {
  cleanup();
  client.clear();
  vi.useRealTimers();
  expect(api.post).not.toHaveBeenCalled();
  expect(api.get.mock.calls.every(([url]) => url === endpoints.gateway.requestLogs)).toBe(true);
});

describe("per-route traffic reads", () => {
  it("keeps successful metrics and retries only the failed route", async () => {
    let fail = true;
    api.get.mockImplementation(async (_url, { params }) => {
      if (params.provider && fail) throw new Error("read failed");
      return response(params);
    });
    const { result } = open();
    await waitFor(() => expect(result.current.error).toBeInstanceOf(Error));
    await settled(result);
    expect(result.current.isLoading).toBe(false);
    expect(result.current.items[0]).toMatchObject({
      status: "success", health: { requests: 5, errors: 1, p50: 120, p95: 400 },
      dataUpdatedAt: Date.parse(FIRST),
    });
    expect(result.current.items[1]).toMatchObject({ status: "error", dataUpdatedAt: 0 });
    api.get.mockClear();
    fail = false;
    await act(async () => { await result.current.items[1].refetch(); });
    await settled(result);
    expect(result.current.error).toBeNull();
    expect(result.current.items[1].health.requests).toBe(5);
    expect(api.get).toHaveBeenCalledTimes(4);
    expect(api.get.mock.calls.every(([, { params }]) => params.provider === "openai")).toBe(true);
  });

  it("refreshes every route with one new explicit 24-hour count/error/latency window", async () => {
    const { result } = open();
    await waitFor(() => expect(result.current.items[0].health?.requests).toBe(5));
    await settled(result);
    api.get.mockClear();
    vi.setSystemTime(new Date(NEXT));
    act(() => { result.current.refreshAll(); });
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(8));
    await settled(result);
    for (const [, { params }] of api.get.mock.calls) {
      expect(params).toMatchObject({
        started_after: "2026-11-08T10:05:37.456Z",
        started_before: NEXT,
        limit: 1,
      });
    }
    expect(api.get.mock.calls.filter(([, { params }]) => params.is_error)).toHaveLength(2);
    expect(api.get.mock.calls.filter(([, { params }]) => params.ordering === "latency_ms")).toHaveLength(4);
    expect(result.current.items.map((item) => item.dataUpdatedAt)).toEqual([
      Date.parse(NEXT), Date.parse(NEXT),
    ]);
    // A deliberate refresh still reads again if the clock has not ticked.
    api.get.mockClear();
    act(() => { result.current.refreshAll(); });
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(8));
  });

  it("retains stale metrics and the successful timestamp across repeated failed refreshes", async () => {
    const { result } = open();
    await waitFor(() => expect(result.current.items[0].health?.requests).toBe(5));
    await settled(result);
    const previous = result.current.items[0].health;
    api.get.mockRejectedValue(new Error("read failed"));
    for (const time of [NEXT, "2026-11-09T10:06:00.000Z"]) {
      vi.setSystemTime(new Date(time));
      act(() => { result.current.refreshAll(); });
      await waitFor(() => expect(result.current.items[0].status).toBe("error"));
      expect(result.current.items[0]).toMatchObject({
        health: previous, isStale: true, dataUpdatedAt: Date.parse(FIRST),
      });
    }
    vi.setSystemTime(new Date("2026-11-09T10:07:00.000Z"));
    api.get.mockImplementation(async (_url, { params }) => response(params, 8));
    await act(async () => { await result.current.items[0].refetch(); });
    await waitFor(() => expect(result.current.items[0].status).toBe("success"));
    expect(result.current.items[0]).toMatchObject({
      health: { requests: 8 }, isStale: false,
      dataUpdatedAt: Date.parse("2026-11-09T10:07:00.000Z"),
    });
    expect(result.current.items[1].dataUpdatedAt).toBe(Date.parse(FIRST));
  });

  it("updates last-checked at successful completion, not when refresh starts", async () => {
    const { result } = open([ROUTES[0]]);
    await waitFor(() => expect(result.current.items[0].health?.requests).toBe(5));
    let finishCount;
    api.get.mockClear();
    api.get.mockImplementationOnce(() => new Promise((resolve) => { finishCount = resolve; }));
    vi.setSystemTime(new Date(NEXT));
    act(() => { result.current.refreshAll(); });
    await waitFor(() => expect(finishCount).toBeTypeOf("function"));
    expect(result.current.items[0]).toMatchObject({
      isFetching: true, isStale: true, health: { requests: 5 },
      dataUpdatedAt: Date.parse(FIRST),
    });
    const completed = "2026-11-09T10:06:00.000Z";
    vi.setSystemTime(new Date(completed));
    await act(async () => { finishCount({ data: { count: 7, results: [] } }); });
    await waitFor(() => expect(result.current.items[0].health?.requests).toBe(7));
    expect(result.current.items[0]).toMatchObject({
      isStale: false, dataUpdatedAt: Date.parse(completed),
    });
    expect(api.get).toHaveBeenCalledTimes(4);
    expect(api.get.mock.calls.every(([, { params }]) => params.started_before === NEXT)).toBe(true);
  });

  it("resets the window on a model change and ignores the old model's late response", async () => {
    let finishOld;
    api.get.mockImplementation((_url, { params }) => {
      if (params.model.includes("gpt-4o")) {
        return new Promise((resolve) => { finishOld = resolve; });
      }
      return Promise.resolve({ data: { count: 0, results: [] } });
    });
    const { result, rerender } = open([ROUTES[1]]);
    await waitFor(() => expect(finishOld).toBeTypeOf("function"));
    vi.setSystemTime(new Date(NEXT));
    const routes = [{ ...ROUTES[1], sendAs: "gpt-4o-mini" }];
    // New reads can finish while the old model's count is still pending.
    api.get.mockImplementation(async (_url, { params }) => response(params, 2));
    rerender({ routes, modelId: "openai/gpt-4o-mini" });
    expect(result.current.items[0].health).toBeUndefined();
    await waitFor(() => expect(result.current.items[0].health?.requests).toBe(2));
    const newCalls = api.get.mock.calls.filter(([, { params }]) => params.model.includes("gpt-4o-mini"));
    expect(newCalls).toHaveLength(4);
    expect(newCalls.every(([, { params }]) => params.started_before === NEXT)).toBe(true);
    await act(async () => { finishOld({ data: { count: 0, results: [] } }); });
    expect(result.current.items[0].health.requests).toBe(2);
    expect(result.current.items[0].route.sendAs).toBe("gpt-4o-mini");
  });

  it("resets the window when routes change but not when equivalent routes are reallocated", async () => {
    const { result, rerender } = open([ROUTES[0]]);
    await waitFor(() => expect(result.current.items[0].health?.requests).toBe(5));
    await settled(result);
    api.get.mockClear();
    vi.setSystemTime(new Date(NEXT));
    rerender({ routes: [{ ...ROUTES[0] }], modelId: MODEL });
    expect(api.get).not.toHaveBeenCalled();
    rerender({ routes: ROUTES, modelId: MODEL });
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(8));
    expect(api.get.mock.calls.every(([, { params }]) => params.started_before === NEXT)).toBe(true);
    await settled(result);
  });
});
