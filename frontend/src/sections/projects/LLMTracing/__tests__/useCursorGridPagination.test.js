import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import useCursorGridPagination from "../useCursorGridPagination";

const publish = (result, { startRow, endRow, rows, isLastPage, metadata }) => {
  let returned;
  act(() => {
    returned = result.current.publishPage({
      request: { startRow, endRow },
      rows: new Array(rows).fill({}),
      isLastPage,
      metadata,
    });
  });
  return returned;
};

describe("useCursorGridPagination", () => {
  it("keeps the AG Grid row count sentinel unchanged", () => {
    // REGRESSION GUARD: this value feeds params.success({ rowCount }). If it
    // stops being endRow + 1 on a non-terminal page, AG Grid concludes the
    // data ends here and forward navigation dies silently.
    const { result } = renderHook(() => useCursorGridPagination(null, null));

    expect(
      publish(result, {
        startRow: 0,
        endRow: 25,
        rows: 25,
        isLastPage: false,
        metadata: { total_rows: 26, has_more: true },
      }),
    ).toBe(26);

    expect(
      publish(result, {
        startRow: 25,
        endRow: 50,
        rows: 25,
        isLastPage: false,
        metadata: { total_rows: 51, has_more: true },
      }),
    ).toBe(51);
  });

  it("keeps pageCount as the navigation bound", () => {
    const { result } = renderHook(() => useCursorGridPagination(null, null));
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: { total_rows: 26, has_more: true },
    });
    expect(result.current.pageCount).toBe(2);
    expect(result.current.page).toBe(1);
  });

  it("proves the next page when the metadata proves a further row", () => {
    const { result } = renderHook(() => useCursorGridPagination(null, null));
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: {
        total_rows: 26,
        total_rows_is_lower_bound: true,
        has_more: true,
      },
    });
    expect(result.current.hasMore).toBe(true);
    expect(result.current.provenNext).toBe(true);
  });

  it("withholds the next page number when no further row is proven", () => {
    const { result } = renderHook(() => useCursorGridPagination(null, null));
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: {
        total_count: 25,
        count_is_lower_bound: true,
        has_more: true,
      },
    });
    expect(result.current.hasMore).toBe(true);
    expect(result.current.provenNext).toBe(false);
  });

  it("clears both flags on a terminal page", () => {
    const { result } = renderHook(() => useCursorGridPagination(null, null));
    publish(result, {
      startRow: 50,
      endRow: 75,
      rows: 9,
      isLastPage: true,
      metadata: {
        total_rows: 59,
        total_rows_is_lower_bound: false,
        has_more: false,
      },
    });
    expect(result.current.hasMore).toBe(false);
    expect(result.current.provenNext).toBe(false);
    expect(result.current.page).toBe(3);
  });

  it("overrides contradictory metadata on a terminal page", () => {
    // REGRESSION GUARD: the server can legitimately report has_more/a higher
    // total on the same response that also sets isLastPage. isLastPage must
    // win regardless of what the metadata says, otherwise the pager could
    // draw a page number the cursor protocol can never serve.
    const { result } = renderHook(() => useCursorGridPagination(null, null));
    publish(result, {
      startRow: 50,
      endRow: 75,
      rows: 9,
      isLastPage: true,
      metadata: {
        total_rows: 100,
        total_rows_is_lower_bound: true,
        has_more: true,
      },
    });
    expect(result.current.hasMore).toBe(false);
    expect(result.current.provenNext).toBe(false);
  });

  it("clears both flags on reset", () => {
    const { result } = renderHook(() => useCursorGridPagination(null, null));
    publish(result, {
      startRow: 0,
      endRow: 25,
      rows: 25,
      isLastPage: false,
      metadata: { total_rows: 26, has_more: true },
    });
    act(() => result.current.resetPagination({ moveGrid: false }));
    expect(result.current.hasMore).toBe(false);
    expect(result.current.provenNext).toBe(false);
    expect(result.current.page).toBe(1);
  });
});
