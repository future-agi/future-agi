import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { endActionTimer, startActionTimer } from "src/utils/actionTimer";
import {
  ERROR_FEED_LIST_TIMER,
  listChangeTrigger,
  useErrorFeedListTimer,
} from "../errorFeedActionTimers";

vi.mock("src/utils/actionTimer", () => ({
  startActionTimer: vi.fn(),
  endActionTimer: vi.fn(),
}));

const base = {
  sort_by: "last_seen",
  sort_dir: "desc",
  limit: 25,
  offset: 0,
};

describe("listChangeTrigger", () => {
  it("names what changed", () => {
    expect(listChangeTrigger(base, { ...base, offset: 25 })).toBe("page");
    expect(listChangeTrigger(base, { ...base, search: "x" })).toBe("search");
    expect(listChangeTrigger(base, { ...base, sort_dir: "asc" })).toBe("sort");
    expect(listChangeTrigger(base, { ...base, time_range_days: 7 })).toBe(
      "time_range",
    );
    expect(listChangeTrigger(base, { ...base, status: "open" })).toBe("filter");
  });

  it("ignores the page reset that a filter change causes", () => {
    const paged = { ...base, offset: 50 };
    expect(listChangeTrigger(paged, { ...base, status: "open" })).toBe(
      "filter",
    );
  });
});

describe("useErrorFeedListTimer", () => {
  beforeEach(() => {
    vi.mocked(startActionTimer).mockClear();
    vi.mocked(endActionTimer).mockClear();
  });

  it("starts nothing on first render", () => {
    renderHook(() => useErrorFeedListTimer(base, false, false));
    expect(startActionTimer).not.toHaveBeenCalled();
  });

  it("starts on a params change and ends when the fetch settles", () => {
    const { rerender } = renderHook(
      ({ params, fetching }) => useErrorFeedListTimer(params, fetching, false),
      { initialProps: { params: base, fetching: false } },
    );
    vi.mocked(endActionTimer).mockClear();
    const next = { ...base, status: "open" };
    rerender({ params: next, fetching: true });
    expect(startActionTimer).toHaveBeenCalledWith(ERROR_FEED_LIST_TIMER, {
      trigger: "filter",
    });
    expect(endActionTimer).not.toHaveBeenCalledWith(
      ERROR_FEED_LIST_TIMER,
      "ok",
    );

    rerender({ params: next, fetching: false });
    expect(endActionTimer).toHaveBeenCalledWith(ERROR_FEED_LIST_TIMER, "ok");
  });

  it("reports a failed fetch as an error", () => {
    const { rerender } = renderHook(
      ({ params, fetching, error }) =>
        useErrorFeedListTimer(params, fetching, error),
      { initialProps: { params: base, fetching: false, error: false } },
    );
    const next = { ...base, search: "x" };
    rerender({ params: next, fetching: true, error: false });
    rerender({ params: next, fetching: false, error: true });
    expect(endActionTimer).toHaveBeenLastCalledWith(
      ERROR_FEED_LIST_TIMER,
      "error",
    );
  });
});
