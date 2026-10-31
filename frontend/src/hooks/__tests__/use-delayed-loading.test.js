import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useDelayedLoading } from "../use-delayed-loading";

const render = (loading) =>
  renderHook(
    ({ value }) => useDelayedLoading(value, { delay: 200, minDuration: 400 }),
    {
      initialProps: { value: loading },
    },
  );

describe("useDelayedLoading", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("never shows a loader for a request that finishes inside the delay", () => {
    const { result, rerender } = render(true);
    act(() => vi.advanceTimersByTime(150));
    expect(result.current).toBe(false);
    rerender({ value: false });
    act(() => vi.advanceTimersByTime(1000));
    expect(result.current).toBe(false);
  });

  it("shows the loader once the request outlasts the delay", () => {
    const { result } = render(true);
    act(() => vi.advanceTimersByTime(199));
    expect(result.current).toBe(false);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe(true);
  });

  it("keeps a shown loader up for the minimum duration", () => {
    const { result, rerender } = render(true);
    act(() => vi.advanceTimersByTime(200));
    rerender({ value: false });
    act(() => vi.advanceTimersByTime(399));
    expect(result.current).toBe(true);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe(false);
  });

  it("hides at once when the loader has already been up long enough", () => {
    const { result, rerender } = render(true);
    act(() => vi.advanceTimersByTime(800));
    rerender({ value: false });
    expect(result.current).toBe(false);
  });

  it("stays up when a new load starts before it hides", () => {
    const { result, rerender } = render(true);
    act(() => vi.advanceTimersByTime(200));
    rerender({ value: false });
    act(() => vi.advanceTimersByTime(100));
    rerender({ value: true });
    act(() => vi.advanceTimersByTime(1000));
    expect(result.current).toBe(true);
  });
});
