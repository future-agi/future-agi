import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCallExecutionV3Detail } from "../runDetail";
import { useCallExecutionDetail } from "src/sections/agents/helper";
import axios, { endpoints } from "src/utils/axios";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn() },
}));

const now = new Date("2026-10-03T12:00:00Z");
const pending = (deadline = "2026-10-03T12:30:00Z") => ({
  id: "call-audio",
  audio_metrics: { schema_version: 1, state: "pending", deadline_at: deadline },
});
const clients = [];

beforeEach(() => {
  vi.useFakeTimers({
    toFake: [
      "Date",
      "setTimeout",
      "clearTimeout",
      "setInterval",
      "clearInterval",
    ],
  });
  vi.setSystemTime(now);
  axios.get.mockReset();
});
afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  vi.useRealTimers();
});

describe.each([
  [
    "v3",
    useCallExecutionV3Detail,
    "simulation-call-detail-v3",
    (data) => data,
    endpoints.runResultsV3.callDetail,
  ],
  [
    "legacy",
    useCallExecutionDetail,
    "callExecutionDetail",
    (data) => ({ data }),
    endpoints.runTests.callExecutionDetail,
  ],
])(
  "%s acoustic polling (R09 / AC10)",
  (_name, useDetail, key, cacheData, endpoint) => {
    async function setup(data = pending()) {
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: Infinity } },
      });
      clients.push(client);
      axios.get.mockResolvedValue({ data });
      const wrapper = ({ children }) =>
        React.createElement(QueryClientProvider, { client }, children);
      const hook = renderHook(
        ({ enabled }) => useDetail("call-audio", enabled),
        {
          wrapper,
          initialProps: { enabled: true },
        },
      );
      await act(() => vi.advanceTimersByTimeAsync(1));
      expect(hook.result.current.data).toEqual(data);
      expect(hook.result.current.isError).toBe(false);
      const query = client
        .getQueryCache()
        .find({ queryKey: [key, "call-audio"] });
      return { ...hook, query, client };
    }

    it("polls the existing detail endpoint every 5 seconds without eval metrics", async () => {
      const { query, unmount } = await setup();
      expect(axios.get).toHaveBeenCalledWith(endpoint("call-audio"));
      expect(query.options.refetchInterval(query)).toBe(5000);
      await act(() => vi.advanceTimersByTimeAsync(5000));
      expect(axios.get).toHaveBeenCalledTimes(2);
      unmount();
    });

    it.each([
      "complete",
      "partial",
      "unavailable",
      "failed",
      "not_requested",
      "unsupported",
    ])("stops when the envelope becomes %s", async (state) => {
      const { query, client, unmount } = await setup();
      act(() =>
        client.setQueryData(
          query.queryKey,
          cacheData({ audio_metrics: { state } }),
        ),
      );
      expect(query.options.refetchInterval(query)).toBe(false);
      await act(() => vi.advanceTimersByTimeAsync(10000));
      expect(axios.get).toHaveBeenCalledTimes(1);
      unmount();
    });

    it.each(["2026-10-03T11:59:59Z", "2026-10-03T12:00:00Z", "invalid-date"])(
      "does not poll past or at an invalid deadline: %s",
      async (deadline) => {
        const { query, unmount } = await setup(pending(deadline));
        expect(query.options.refetchInterval(query)).toBe(false);
        unmount();
      },
    );

    it("keeps polling a pending envelope with a null deadline per design 10.1", async () => {
      const { query, unmount } = await setup(pending(null));
      expect(query.options.refetchInterval(query)).toBe(5000);
      unmount();
    });

    it.each(["pending", "running"])(
      "preserves the 3 second localizer interval (%s)",
      async (status) => {
        const { query, unmount } = await setup({
          ...pending(),
          eval_metrics: { eval1: { error_localizer_status: status } },
        });
        expect(query.options.refetchInterval(query)).toBe(3000);
        unmount();
      },
    );

    it("keeps localizer polling after acoustic metrics finish", async () => {
      const { query, unmount } = await setup({
        audio_metrics: { state: "complete" },
        eval_metrics: { eval1: { error_localizer_status: "running" } },
      });
      expect(query.options.refetchInterval(query)).toBe(3000);
      unmount();
    });

    it("does not poll absent acoustic metrics or finished localizers", async () => {
      const { query, unmount } = await setup({
        eval_metrics: { eval1: { error_localizer_status: "completed" } },
      });
      expect(query.options.refetchInterval(query)).toBe(false);
      unmount();
    });

    it("stops polling when disabled or unmounted", async () => {
      const { rerender, unmount, query } = await setup();
      rerender({ enabled: false });
      await act(() => vi.advanceTimersByTimeAsync(10000));
      expect(axios.get).toHaveBeenCalledTimes(1);
      expect(query.isActive()).toBe(false);
      rerender({ enabled: true });
      await act(() => vi.advanceTimersByTimeAsync(5000));
      expect(axios.get).toHaveBeenCalledTimes(2);
      unmount();
      await act(() => vi.advanceTimersByTimeAsync(10000));
      expect(axios.get).toHaveBeenCalledTimes(2);
      expect(query.getObserversCount()).toBe(0);
    });

    it.each([401, 403, 404])(
      "stops refetches and retries on access loss (%s), retaining cached values",
      async (statusCode) => {
        const data = pending();
        const { query, result, unmount } = await setup(data);
        axios.get.mockRejectedValue({ statusCode });
        await act(() => result.current.refetch());
        await act(() => vi.advanceTimersByTimeAsync(1));
        expect(result.current.isError).toBe(true);
        expect(result.current.data).toEqual(data);
        expect(query.options.refetchInterval(query)).toBe(false);
        expect(query.options.retry(0, { statusCode })).toBe(false);
        expect(
          query.options.retry(0, { response: { status: statusCode } }),
        ).toBe(false);
        await act(() => vi.advanceTimersByTimeAsync(15000));
        expect(axios.get).toHaveBeenCalledTimes(2);
        unmount();
      },
    );
  },
);
