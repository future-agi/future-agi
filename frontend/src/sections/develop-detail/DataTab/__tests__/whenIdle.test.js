import { describe, expect, it, vi } from "vitest";
import { whenIdle } from "../whenIdle";

describe("whenIdle", () => {
  it("uses requestIdleCallback with a deadline", () => {
    const calls = [];
    const scope = {
      requestIdleCallback: (cb, opts) => {
        calls.push(opts);
        cb();
        return 7;
      },
      cancelIdleCallback: vi.fn(),
    };
    const fn = vi.fn();
    const cancel = whenIdle(fn, { timeout: 900, scope });
    expect(fn).toHaveBeenCalledTimes(1);
    expect(calls).toEqual([{ timeout: 900 }]);
    cancel();
    expect(scope.cancelIdleCallback).toHaveBeenCalledWith(7);
  });
  it("falls back to a timer", () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    whenIdle(fn, { scope: {} });
    expect(fn).not.toHaveBeenCalled();
    vi.advanceTimersByTime(300);
    expect(fn).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});
