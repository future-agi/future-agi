import React from "react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RouteSkeleton from "../RouteSkeleton";
import { SLOW_HINT_MS, VERY_SLOW_HINT_MS, slowHintText } from "../slowHint";

describe("slowHintText", () => {
  it("says nothing for the first 3 s", () => {
    expect(slowHintText(0)).toBeNull();
    expect(slowHintText(SLOW_HINT_MS - 1)).toBeNull();
  });
  it("says it is still loading, then why", () => {
    expect(slowHintText(SLOW_HINT_MS)).toBe("Still loading this page…");
    expect(slowHintText(VERY_SLOW_HINT_MS)).toMatch(
      /^Still loading\. The first visit after an update takes longer/,
    );
  });
});

describe("RouteSkeleton", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("first skeleton after a load counts from the navigation", () => {
    // 2 s already passed since the reload (boot) when the skeleton mounts.
    vi.spyOn(performance, "now").mockReturnValue(2000);
    const { unmount } = render(<RouteSkeleton />);
    const line = screen.getByTestId("route-skeleton-hint");
    expect(line).toHaveTextContent("");
    act(() => vi.advanceTimersByTime(1000));
    expect(line).toHaveTextContent("Still loading this page…");
    unmount();
  });

  it("later skeletons count from their mount, in a reserved line", () => {
    render(<RouteSkeleton />);
    const line = screen.getByTestId("route-skeleton-hint");
    expect(line).toHaveTextContent("");
    act(() => vi.advanceTimersByTime(SLOW_HINT_MS - 1));
    expect(line).toHaveTextContent("");
    act(() => vi.advanceTimersByTime(1));
    expect(line).toHaveTextContent("Still loading this page…");
    act(() => vi.advanceTimersByTime(VERY_SLOW_HINT_MS));
    expect(line).toHaveTextContent(/takes longer/);
  });
});
