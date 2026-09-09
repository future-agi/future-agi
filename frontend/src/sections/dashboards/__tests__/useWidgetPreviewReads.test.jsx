import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useWidgetPreviewReads } from "../useWidgetPreviewReads";
const config = { metrics: [{ name: "Latency", aggregation: "avg" }] };
const signature = JSON.stringify(config);
const exact = (value) => ({
  data: {
    result: {
      query_complete: true,
      query_status: "complete",
      query_sampled: false,
      metrics: [
        {
          name: "Latency",
          aggregation: "avg",
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
          series: [
            {
              name: "total",
              data: [{ timestamp: "2026-07-09T00:00:00Z", value }],
            },
          ],
        },
      ],
    },
  },
});
afterEach(() => vi.useRealTimers());
describe("widget editor exact read lifecycle", () => {
  it("accepts an exact result after a slow read without a browser timeout failure", async () => {
    vi.useFakeTimers();
    const mutate = vi.fn();
    const { result } = renderHook(() =>
      useWidgetPreviewReads(mutate, signature),
    );
    act(() => result.current.runPreviewQuery(config));
    await act(async () => vi.advanceTimersByTimeAsync(120_000));
    expect(result.current.previewFailed).toBe(false);
    expect(mutate.mock.calls[0][0].signal.aborted).toBe(false);
    act(() => mutate.mock.calls[0][1].onSuccess(exact(12)));
    expect(result.current.lastExactPreview?.result).toEqual(
      exact(12).data.result,
    );
  });
  it("aborts on scope change and prevents a late old result from replacing the new result", () => {
    const mutate = vi.fn();
    const next = { metrics: [{ name: "Cost", aggregation: "sum" }] };
    const { result, rerender } = renderHook(
      ({ scope }) => useWidgetPreviewReads(mutate, scope),
      { initialProps: { scope: signature } },
    );
    act(() => result.current.runPreviewQuery(config));
    rerender({ scope: JSON.stringify(next) });
    expect(mutate.mock.calls[0][0].signal.aborted).toBe(true);
    act(() => result.current.runPreviewQuery(next));
    act(() => mutate.mock.calls[1][1].onSuccess(exact(24)));
    act(() => mutate.mock.calls[0][1].onSuccess(exact(999)));
    expect(result.current.lastExactPreview?.signature).toBe(
      JSON.stringify(next),
    );
    expect(result.current.lastExactPreview?.result).toEqual(
      exact(24).data.result,
    );
  });
  it("preserves the exact snapshot during a slow refresh and aborts on unmount", async () => {
    vi.useFakeTimers();
    const mutate = vi.fn();
    const { result, unmount } = renderHook(() =>
      useWidgetPreviewReads(mutate, signature),
    );
    act(() => result.current.runPreviewQuery(config));
    act(() => mutate.mock.calls[0][1].onSuccess(exact(12)));
    act(() => result.current.runPreviewQuery(config, { refresh: true }));
    await act(async () => vi.advanceTimersByTimeAsync(120_000));
    expect(result.current.previewFailed).toBe(false);
    expect(result.current.lastExactPreview?.result).toEqual(
      exact(12).data.result,
    );
    expect(mutate.mock.calls[1][0].signal.aborted).toBe(false);
    unmount();
    expect(mutate.mock.calls[1][0].signal.aborted).toBe(true);
  });
});

it("pauses polling a pending exact job without a query failure and continues explicitly", async () => {
  vi.useFakeTimers();
  const pending = {
    data: {
      result: {
        metrics: [],
        query_complete: false,
        query_status: "pending",
        query_sampled: false,
        query_refreshing: true,
      },
    },
  };
  const mutate = vi.fn((_request, options) => options.onSuccess(pending));
  const { result } = renderHook(() => useWidgetPreviewReads(mutate, signature));
  act(() => result.current.runPreviewQuery(config));
  await act(async () => vi.advanceTimersByTimeAsync(500_000));
  expect(result.current.previewPollingPaused).toBe(true);
  expect(result.current.previewFailed).toBe(false);
  const count = mutate.mock.calls.length;
  await act(async () => vi.advanceTimersByTimeAsync(500_000));
  expect(mutate).toHaveBeenCalledTimes(count);
  mutate.mockImplementation((_request, options) =>
    options.onSuccess(exact(24)),
  );
  act(() => result.current.runPreviewQuery(config, { refresh: true }));
  expect(result.current.previewPollingPaused).toBe(false);
  expect(result.current.lastExactPreview?.result).toEqual(
    exact(24).data.result,
  );
});
