import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSearchedPagination } from "../common";

// common.js also exports API hooks; keep the axios instance out of the test.
vi.mock("src/utils/axios", () => ({
  default: {},
  endpoints: {},
}));

describe("useSearchedPagination", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const flushDebounce = () => {
    act(() => {
      vi.advanceTimersByTime(500);
    });
  };

  it("starts on page 1 with the default page size", () => {
    const { result } = renderHook(() => useSearchedPagination());
    flushDebounce();
    expect(result.current.pagination).toEqual({ page: 1, pageSize: 10 });
    expect(result.current.debouncedSearch).toBe("");
  });

  // #1485: searching from page 2+ must not request the stale page.
  it("resets to page 1 when the debounced search term changes", () => {
    const { result } = renderHook(() => useSearchedPagination());
    flushDebounce();

    act(() => {
      result.current.setPagination((prev) => ({ ...prev, page: 3 }));
    });
    expect(result.current.pagination.page).toBe(3);

    act(() => {
      result.current.setSearch("morg");
    });
    flushDebounce();

    expect(result.current.debouncedSearch).toBe("morg");
    expect(result.current.pagination).toEqual({ page: 1, pageSize: 10 });
  });

  it("resets to page 1 when the search box is cleared", () => {
    const { result } = renderHook(() => useSearchedPagination());
    flushDebounce();

    act(() => {
      result.current.setSearch("morg");
    });
    flushDebounce();
    act(() => {
      result.current.setPagination((prev) => ({ ...prev, page: 2 }));
    });

    act(() => {
      result.current.setSearch("");
    });
    flushDebounce();

    expect(result.current.pagination.page).toBe(1);
  });

  it("preserves a changed page size across the reset", () => {
    const { result } = renderHook(() => useSearchedPagination());
    flushDebounce();

    act(() => {
      result.current.setPagination({ page: 4, pageSize: 25 });
    });

    act(() => {
      result.current.setSearch("abc");
    });
    flushDebounce();

    expect(result.current.pagination).toEqual({ page: 1, pageSize: 25 });
  });

  it("keeps the same state object when already on page 1 (no extra render)", () => {
    const { result } = renderHook(() => useSearchedPagination());
    flushDebounce();
    const before = result.current.pagination;

    act(() => {
      result.current.setSearch("abc");
    });
    flushDebounce();

    expect(result.current.pagination).toBe(before);
  });

  it("does not reset until the debounce delay has elapsed", () => {
    const { result } = renderHook(() => useSearchedPagination());
    flushDebounce();

    act(() => {
      result.current.setPagination((prev) => ({ ...prev, page: 2 }));
    });
    act(() => {
      result.current.setSearch("mo");
    });
    act(() => {
      vi.advanceTimersByTime(499);
    });
    expect(result.current.pagination.page).toBe(2);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current.pagination.page).toBe(1);
  });

  it("leaves plain page navigation untouched", () => {
    const { result } = renderHook(() => useSearchedPagination());
    flushDebounce();

    act(() => {
      result.current.setPagination((prev) => ({ ...prev, page: 2 }));
    });
    act(() => {
      result.current.setPagination((prev) => ({ ...prev, page: 5 }));
    });

    expect(result.current.pagination.page).toBe(5);
  });
});
