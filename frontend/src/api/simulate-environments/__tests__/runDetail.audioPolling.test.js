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
// Both detail APIs serialize start_time and duration_seconds; envelope reasons
// live on the metric entries, not on the envelope itself.
const notAnalyzed = (reason = "not_analyzed") => ({
  id: "call-audio",
  status: "completed",
  start_time: "2026-10-03T11:58:30Z",
  duration: 60,
  duration_seconds: 60,
  audio_metrics: {
    schema_version: 1,
    state: "not_requested",
    metrics: Object.fromEntries(
      ["average_pitch_hz", "estimated_snr_db", "voice_quality_index"].map(
        (name) => [name, { value: null, state: "unavailable", reason }],
      ),
    ),
  },
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
      return { ...hook, query, client, wrapper };
    }

    it("polls the existing detail endpoint every 5 seconds without eval metrics", async () => {
      const { query, unmount } = await setup();
      expect(axios.get).toHaveBeenCalledWith(endpoint("call-audio"));
      expect(query.options.refetchInterval(query)).toBe(5000);
      await act(() => vi.advanceTimersByTimeAsync(5000));
      expect(axios.get).toHaveBeenCalledTimes(2);
      unmount();
    });

    it("polls the scheduling gap through pending to complete for a recent completed call", async () => {
      const data = notAnalyzed();
      const { query, result, unmount } = await setup(data);
      expect(query.options.refetchInterval(query)).toBe(5000);
      await act(() => vi.advanceTimersByTimeAsync(5000));
      expect(axios.get).toHaveBeenCalledTimes(2);
      expect(result.current.data).toEqual(data);
      axios.get.mockResolvedValue({ data: pending() });
      await act(() => vi.advanceTimersByTimeAsync(5001));
      expect(result.current.data).toEqual(pending());
      expect(query.options.refetchInterval(query)).toBe(5000);
      const complete = { audio_metrics: { state: "complete" } };
      axios.get.mockResolvedValue({ data: complete });
      await act(() => vi.advanceTimersByTimeAsync(5001));
      expect(result.current.data).toEqual(complete);
      expect(query.options.refetchInterval(query)).toBe(false);
      await act(() => vi.advanceTimersByTimeAsync(15000));
      expect(axios.get).toHaveBeenCalledTimes(4);
      unmount();
    });

    it("bounds the scheduling gap to two minutes from call completion", async () => {
      const { query, unmount } = await setup({
        ...notAnalyzed(),
        start_time: "2026-10-03T11:57:10Z",
      });
      expect(query.options.refetchInterval(query)).toBe(5000);
      await act(() => vi.advanceTimersByTimeAsync(5000));
      expect(axios.get).toHaveBeenCalledTimes(2);
      await act(() => vi.advanceTimersByTimeAsync(5000));
      expect(query.options.refetchInterval(query)).toBe(false);
      expect(axios.get).toHaveBeenCalledTimes(3);
      await act(() => vi.advanceTimersByTimeAsync(15000));
      expect(axios.get).toHaveBeenCalledTimes(3);
      unmount();
    });

    it.each(["ended_at", "completed_at"])(
      "uses an explicit recent %s when provided",
      async (field) => {
        const { query, unmount } = await setup({
          ...notAnalyzed(),
          start_time: null,
          duration_seconds: null,
          duration: null,
          [field]: "2026-10-03T11:59:30Z",
        });
        expect(query.options.refetchInterval(query)).toBe(5000);
        unmount();
      },
    );

    it.each([
      [
        "historical call recently updated",
        { start_time: "2026-10-02T11:58:30Z", timestamp: now.toISOString() },
      ],
      ["exact window boundary", { start_time: "2026-10-03T11:57:00Z" }],
      ["ongoing call", { status: "ongoing" }],
      ["failed call", { status: "failed" }],
      ["unknown call status", { status: undefined }],
      ["snapshot", { is_snapshot: true }],
      ["missing start time", { start_time: null }],
      ["invalid start time", { start_time: "invalid-date" }],
      ["missing duration", { duration_seconds: null, duration: null }],
      ["negative duration", { duration_seconds: -1 }],
      ["invalid duration", { duration_seconds: "invalid" }],
      ["future completion", { start_time: "2026-10-03T12:01:00Z" }],
      ["invalid explicit end time", { ended_at: "invalid-date" }],
      ["historical explicit end time", { ended_at: "2026-10-02T12:00:00Z" }],
      ["missing envelope", { audio_metrics: null }],
      ["missing reasons", { audio_metrics: { state: "not_requested" } }],
    ])("does not poll the scheduling gap for %s", async (_case, fields) => {
      const { query, unmount } = await setup({ ...notAnalyzed(), ...fields });
      expect(query.options.refetchInterval(query)).toBe(false);
      await act(() => vi.advanceTimersByTimeAsync(15000));
      expect(axios.get).toHaveBeenCalledTimes(1);
      unmount();
    });

    it.each(["not_enabled", "not_applicable", "unsupported_schema_version"])(
      "does not treat %s as a scheduling gap even for a recent call",
      async (reason) => {
        const { query, unmount } = await setup(notAnalyzed(reason));
        expect(query.options.refetchInterval(query)).toBe(false);
        await act(() => vi.advanceTimersByTimeAsync(15000));
        expect(axios.get).toHaveBeenCalledTimes(1);
        unmount();
      },
    );

    it("preserves localizer precedence during the scheduling gap", async () => {
      const { query, unmount } = await setup({
        ...notAnalyzed(),
        eval_metrics: { eval1: { error_localizer_status: "running" } },
      });
      expect(query.options.refetchInterval(query)).toBe(3000);
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

    it.each(["2026-10-03T11:59:54Z", "2026-10-03T11:59:55Z", "invalid-date"])(
      "does not poll outside the deadline grace or with an invalid deadline: %s",
      async (deadline) => {
        const { query, unmount } = await setup(pending(deadline));
        expect(query.options.refetchInterval(query)).toBe(false);
        await act(() => vi.advanceTimersByTimeAsync(15000));
        expect(axios.get).toHaveBeenCalledTimes(1);
        unmount();
      },
    );

    it.each(["2026-10-03T11:59:56Z", "2026-10-03T12:00:00Z"])(
      "allows only one final refresh inside the deadline grace: %s",
      async (deadline) => {
        const { query, unmount } = await setup(pending(deadline));
        expect(query.options.refetchInterval(query)).toBe(5000);
        await act(() => vi.advanceTimersByTimeAsync(5000));
        expect(axios.get).toHaveBeenCalledTimes(2);
        expect(query.options.refetchInterval(query)).toBe(false);
        await act(() => vi.advanceTimersByTimeAsync(15000));
        expect(axios.get).toHaveBeenCalledTimes(2);
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

    it.each([{ response: { status: 500 } }, new Error("Network unavailable")])(
      "retains stale data after a transient error and recovers on the next poll (%j)",
      async (error) => {
        const data = pending();
        const { query, result, unmount } = await setup(data);
        axios.get.mockRejectedValueOnce(error);
        await act(() => result.current.refetch());
        await act(() => vi.advanceTimersByTimeAsync(1));
        expect(result.current.isError).toBe(true);
        expect(result.current.data).toEqual(data);
        expect(query.options.refetchInterval(query)).toBe(5000);
        const complete = { ...data, audio_metrics: { state: "complete" } };
        axios.get.mockResolvedValue({ data: complete });
        await act(() => vi.advanceTimersByTimeAsync(5001));
        expect(result.current.data).toEqual(complete);
        expect(result.current.isError).toBe(false);
        expect(query.options.refetchInterval(query)).toBe(false);
        unmount();
      },
    );

    it("does not promise continued polling after a failed terminal refresh", async () => {
      const data = { audio_metrics: { state: "complete" } };
      const { query, result, unmount } = await setup(data);
      axios.get.mockRejectedValue(new Error("Network unavailable"));
      await act(() => result.current.refetch());
      await act(() => vi.advanceTimersByTimeAsync(1));
      expect(result.current.isError).toBe(true);
      expect(result.current.data).toEqual(data);
      expect(query.options.refetchInterval(query)).toBe(false);
      await act(() => vi.advanceTimersByTimeAsync(15000));
      expect(axios.get).toHaveBeenCalledTimes(2);
      unmount();
    });

    it.each(["pending", "failed", "error"])(
      "refreshes once after a pending response crosses the deadline, then stops (%s)",
      async (outcome) => {
        const data = pending("2026-10-03T12:00:06Z");
        const { query, result, unmount } = await setup(data);
        let finish;
        axios.get.mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              finish = resolve;
            }),
        );
        await act(() => vi.advanceTimersByTimeAsync(5000));
        expect(axios.get).toHaveBeenCalledTimes(2);
        // The server serialized pending before expiry; delivery crosses it.
        await act(() => vi.advanceTimersByTimeAsync(2000));
        await act(async () => finish({ data }));
        expect(query.options.refetchInterval(query)).toBe(5000);
        const finalData = {
          ...data,
          audio_metrics: { ...data.audio_metrics, state: outcome },
        };
        if (outcome === "error") {
          axios.get.mockRejectedValue(new Error("Network unavailable"));
        } else {
          axios.get.mockResolvedValue({ data: finalData });
        }
        await act(() => vi.advanceTimersByTimeAsync(5001));
        expect(axios.get).toHaveBeenCalledTimes(3);
        expect(result.current.data).toEqual(
          outcome === "error" ? data : finalData,
        );
        expect(result.current.isError).toBe(outcome === "error");
        expect(query.options.refetchInterval(query)).toBe(false);
        await act(() => vi.advanceTimersByTimeAsync(15000));
        expect(axios.get).toHaveBeenCalledTimes(3);
        unmount();
      },
    );

    if (_name === "v3") {
      it("preserves scoring precedence during the scheduling gap", async () => {
        const { query, unmount } = await setup({
          ...notAnalyzed(),
          evaluations: [{ status: "pending" }],
        });
        expect(query.options.refetchInterval(query)).toBe(3000);
        unmount();
      });

      it("preserves scoring precedence over audio polling and stops on access loss", async () => {
        const { query, result, unmount } = await setup({
          ...pending(),
          evaluations: [{ status: "pending" }],
        });
        expect(query.options.refetchInterval(query)).toBe(3000);
        axios.get.mockRejectedValue({ statusCode: 403 });
        await act(() => result.current.refetch());
        await act(() => vi.advanceTimersByTimeAsync(1));
        expect(query.options.refetchInterval(query)).toBe(false);
        unmount();
      });
    }

    describe.each([401, 403, 404])("access recovery after %s", (statusCode) => {
      it.each(["reopen", "fresh mount", "invalidate", "explicit refetch"])(
        "recovers via %s with the denied query still cached",
        async (recovery) => {
          const data = pending();
          const hook = await setup(data);
          const { query, result, client, wrapper } = hook;
          axios.get.mockRejectedValue({ response: { status: statusCode } });
          await act(() => result.current.refetch());
          await act(() => vi.advanceTimersByTimeAsync(1));
          expect(result.current.isError).toBe(true);
          expect(result.current.data).toEqual(data);
          expect(query.options.refetchInterval(query)).toBe(false);
          expect(
            query.options.retry(0, { response: { status: statusCode } }),
          ).toBe(false);
          await act(() => vi.advanceTimersByTimeAsync(15000));
          expect(axios.get).toHaveBeenCalledTimes(2);

          axios.get.mockResolvedValue({ data });
          let recovered = hook;
          if (recovery === "reopen") {
            hook.rerender({ enabled: false });
            hook.rerender({ enabled: true });
          } else if (recovery === "fresh mount") {
            hook.unmount();
            expect(
              client.getQueryCache().find({ queryKey: query.queryKey }),
            ).toBe(query);
            recovered = renderHook(() => useDetail("call-audio", true), {
              wrapper,
            });
          } else if (recovery === "invalidate") {
            await act(() =>
              client.invalidateQueries({ queryKey: query.queryKey }),
            );
          } else {
            await act(() => result.current.refetch());
          }
          await act(() => vi.advanceTimersByTimeAsync(1));
          expect(axios.get).toHaveBeenCalledTimes(3);
          expect(recovered.result.current.isError).toBe(false);
          expect(recovered.result.current.data).toEqual(data);
          expect(query.isActive()).toBe(true);
          expect(query.options.refetchInterval(query)).toBe(5000);
          await act(() => vi.advanceTimersByTimeAsync(5000));
          expect(axios.get).toHaveBeenCalledTimes(4);
          recovered.unmount();
        },
      );
    });

    it.each([401, 403, 404])(
      "stops polling and retries on access loss (%s), retaining cached values",
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
