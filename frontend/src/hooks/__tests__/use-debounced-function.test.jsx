import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "src/utils/test-utils";
import logger from "src/utils/logger";
import { useDebouncedFunction } from "../use-debounced-function";

vi.mock("src/utils/logger", () => ({ default: { error: vi.fn() } }));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

const byIndex = (args) => args[0].index;

function mount(fn) {
  return renderHook(() => useDebouncedFunction(fn, 500, byIndex));
}

describe("Unit: useDebouncedFunction", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });
  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("flush invokes a pending call immediately and waits for it to settle", async () => {
    const save = deferred();
    const fn = vi.fn(() => save.promise);
    const { result } = mount(fn);
    result.current({ index: 0, model: "selected" });
    const settled = vi.fn();
    const flushed = result.current.flush(0).then(settled);
    await act(async () => {});
    expect(fn).toHaveBeenCalledExactlyOnceWith({ index: 0, model: "selected" });
    expect(settled).not.toHaveBeenCalled();
    save.resolve("saved");
    await flushed;
    expect(settled).toHaveBeenCalledWith("saved");
    await vi.advanceTimersByTimeAsync(500);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("flush resolves undefined when nothing is pending", async () => {
    const fn = vi.fn();
    const { result } = mount(fn);
    await expect(result.current.flush(0)).resolves.toBeUndefined();
    expect(fn).not.toHaveBeenCalled();
  });

  it("flush awaits a call already started by its timer", async () => {
    const save = deferred();
    const fn = vi.fn(() => save.promise);
    const { result } = mount(fn);
    result.current({ index: 0 });
    await vi.advanceTimersByTimeAsync(500);
    const settled = vi.fn();
    const flushed = result.current.flush(0).then(settled);
    await act(async () => {});
    expect(fn).toHaveBeenCalledTimes(1);
    expect(settled).not.toHaveBeenCalled();
    save.resolve();
    await flushed;
    expect(settled).toHaveBeenCalledTimes(1);
    await expect(result.current.flush(0)).resolves.toBeUndefined();
  });

  it("serializes same-key calls so an older save cannot finish last", async () => {
    const first = deferred();
    const last = deferred();
    const fn = vi
      .fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(last.promise);
    const { result } = mount(fn);
    result.current({ index: 0, model: "first" });
    await vi.advanceTimersByTimeAsync(500);
    result.current({ index: 0, model: "last" });
    await vi.advanceTimersByTimeAsync(500);
    expect(fn).toHaveBeenCalledTimes(1);
    const settled = vi.fn();
    const flushed = result.current.flush(0).then(settled);
    first.resolve();
    await act(async () => {});
    expect(fn).toHaveBeenLastCalledWith({ index: 0, model: "last" });
    expect(fn).toHaveBeenCalledTimes(2);
    expect(settled).not.toHaveBeenCalled();
    last.resolve();
    await flushed;
    expect(settled).toHaveBeenCalledTimes(1);
  });

  it("handles timer rejections and retries the failed call on the next flush", async () => {
    const error = new Error("Save unavailable");
    const fn = vi
      .fn()
      .mockRejectedValueOnce(error)
      .mockResolvedValueOnce("retried");
    // Vitest fails the run on an unhandled rejection; the timer path must log it.
    const { result } = mount(fn);
    result.current({ index: 0, model: "selected" });
    await vi.advanceTimersByTimeAsync(500);
    expect(logger.error).toHaveBeenCalledWith(
      "Debounced function failed",
      error,
    );
    await expect(result.current.flush(0)).resolves.toBe("retried");
    expect(fn).toHaveBeenCalledTimes(2);
    expect(fn).toHaveBeenLastCalledWith({ index: 0, model: "selected" });
    await expect(result.current.flush(0)).resolves.toBeUndefined();
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("rejects a flush that awaited a failing call, then a newer call replaces the retry", async () => {
    const error = new Error("Save unavailable");
    const save = deferred();
    const fn = vi
      .fn()
      .mockReturnValueOnce(save.promise)
      .mockResolvedValueOnce("newer");
    const { result } = mount(fn);
    result.current({ index: 0, model: "old" });
    const flushed = result.current.flush(0);
    save.reject(error);
    await expect(flushed).rejects.toBe(error);
    result.current({ index: 0, model: "new" });
    await expect(result.current.flush(0)).resolves.toBe("newer");
    expect(fn).toHaveBeenCalledTimes(2);
    expect(fn).toHaveBeenLastCalledWith({ index: 0, model: "new" });
  });

  it("flush surfaces a synchronous callback failure too", async () => {
    const error = new Error("Invalid save");
    const { result } = mount(() => {
      throw error;
    });
    result.current({ index: 0 });
    await expect(result.current.flush(0)).rejects.toBe(error);
    await expect(result.current.flush(0)).rejects.toBe(error);
  });

  it("keeps different keys independent", async () => {
    const first = deferred();
    const fn = vi.fn(({ index }) => (index === 0 ? first.promise : "other"));
    const { result } = mount(fn);
    result.current({ index: 0 });
    result.current({ index: 1 });
    const flushed = result.current.flush(0);
    await expect(result.current.flush(1)).resolves.toBe("other");
    expect(fn).toHaveBeenCalledTimes(2);
    first.resolve();
    await flushed;
  });

  it("unmount clears pending timers", async () => {
    const fn = vi.fn();
    const { result, unmount } = mount(fn);
    result.current({ index: 0 });
    result.current({ index: 1 });
    unmount();
    await vi.advanceTimersByTimeAsync(500);
    expect(fn).not.toHaveBeenCalled();
  });

  it("preserves trailing debounce timing and the latest arguments per key", async () => {
    const fn = vi.fn();
    const { result } = mount(fn);
    result.current({ index: 0, model: "old" });
    await vi.advanceTimersByTimeAsync(250);
    result.current({ index: 0, model: "new" });
    result.current({ index: 1, model: "other" });
    await vi.advanceTimersByTimeAsync(499);
    expect(fn).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(fn.mock.calls).toEqual([
      [{ index: 0, model: "new" }],
      [{ index: 1, model: "other" }],
    ]);
  });

  it("keeps the default delay and first-argument key", async () => {
    const fn = vi.fn();
    const { result } = renderHook(() => useDebouncedFunction(fn));
    result.current("key", "old");
    result.current("key", "new");
    await vi.advanceTimersByTimeAsync(999);
    expect(fn).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(fn).toHaveBeenCalledExactlyOnceWith("key", "new");
  });
});
