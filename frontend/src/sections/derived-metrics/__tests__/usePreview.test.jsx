import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const api = vi.hoisted(() => ({ preview: vi.fn() }));
vi.mock("../api", () => ({
  metricsApi: api,
  metricErrorState: (error) =>
    ({ 503: "unavailable", 429: "busy", 409: "superseded" })[
      error.statusCode
    ] || "error",
}));
vi.mock("../useMetricsEnabled", () => ({
  useMetricsEnabled: () => ({ enabled: true }),
}));
import usePreview from "../usePreview";
import { scope } from "./fixtures/builder";
const args = {
  scope,
  definition: { kind: "share" },
  window: {
    window_start: "2026-11-01T00:00:00Z",
    window_end: "2026-11-08T00:00:00Z",
  },
};
const advance = (ms) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
beforeEach(() => {
  vi.useFakeTimers();
  api.preview.mockReset();
  api.preview.mockResolvedValue({ results: [] });
});
afterEach(async () => {
  cleanup();
  await advance(2000);
  vi.useRealTimers();
});
describe("serialized draft previews", () => {
  it("debounces 600ms and uses a fresh real cancellation identity", async () => {
    const { result } = renderHook(() => usePreview(args));
    await advance(599);
    expect(api.preview).not.toHaveBeenCalled();
    await advance(1);
    expect(api.preview).toHaveBeenCalledTimes(1);
    expect(api.preview.mock.calls[0][0]).toMatchObject({
      scope,
      data: {
        definition: args.definition,
        include_samples: true,
        ...args.window,
        request_id: expect.any(String),
      },
      signal: expect.any(AbortSignal),
    });
    expect(result.current.state).toBe("ready");
    act(() => result.current.retry());
    await advance(600);
    expect(api.preview.mock.calls[1][0].data.request_id).not.toBe(
      api.preview.mock.calls[0][0].data.request_id,
    );
  });
  it("aborts immediately on edit and waits for old promise settlement before replacement", async () => {
    let resolveOld;
    api.preview.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    );
    const { result, rerender } = renderHook((props) => usePreview(props), {
      initialProps: args,
    });
    await advance(600);
    const oldSignal = api.preview.mock.calls[0][0].signal;
    rerender({ ...args, definition: { kind: "count" } });
    expect(oldSignal.aborted).toBe(true);
    expect(result.current.data).toBeUndefined();
    await advance(600);
    expect(api.preview).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveOld({ summary: "obsolete" });
    });
    expect(api.preview).toHaveBeenCalledTimes(2);
    expect(result.current.data).not.toEqual({ summary: "obsolete" });
  });
  it("aborts on scope change and unmount; stale values never flash", async () => {
    const { result, rerender, unmount } = renderHook(
      (props) => usePreview(props),
      { initialProps: args },
    );
    await advance(600);
    const signal = api.preview.mock.calls[0][0].signal;
    rerender({ ...args, scope: { ...scope, projectId: "other-project" } });
    expect(signal.aborted).toBe(true);
    expect(result.current.data).toBeUndefined();
    await advance(600);
    unmount();
    expect(api.preview.mock.calls[1][0].signal.aborted).toBe(true);
  });
  it("serializes across unmount and a replacement builder", async () => {
    let settle;
    api.preview.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          settle = resolve;
        }),
    );
    const first = renderHook(() => usePreview(args));
    await advance(600);
    first.unmount();
    renderHook(() => usePreview(args));
    await advance(600);
    expect(api.preview).toHaveBeenCalledTimes(1);
    await act(async () => {
      settle({});
    });
    expect(api.preview).toHaveBeenCalledTimes(2);
  });
  it("retries busy once automatically, then requires an explicit retry", async () => {
    api.preview.mockRejectedValue({ statusCode: 429 });
    const { result } = renderHook(() => usePreview(args));
    await advance(600);
    expect(result.current.state).toBe("busy_retrying");
    await advance(1000);
    expect(api.preview).toHaveBeenCalledTimes(2);
    expect(result.current.state).toBe("busy");
    await advance(5000);
    expect(api.preview).toHaveBeenCalledTimes(2);
  });
  it.each([
    [503, "unavailable"],
    [409, "superseded"],
  ])(
    "keeps HTTP %s as %s without automatic retry",
    async (statusCode, state) => {
      api.preview.mockRejectedValue({ statusCode });
      const { result } = renderHook(() => usePreview(args));
      await advance(600);
      expect(result.current.state).toBe(state);
      expect(result.current.data).toBeUndefined();
      await advance(2000);
      expect(api.preview).toHaveBeenCalledTimes(1);
    },
  );
  it("does not preview an invalid or unavailable definition", async () => {
    const { result } = renderHook(() =>
      usePreview({ ...args, enabled: false }),
    );
    await advance(5000);
    expect(api.preview).not.toHaveBeenCalled();
    expect(result.current.state).toBe("idle");
  });
});
